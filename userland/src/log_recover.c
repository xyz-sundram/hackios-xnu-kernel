/* A separate static PID1 for RAM-only private log recovery. No normal init,
 * launchd, writable skeleton, cleanup, radio, audio or charger commands.
 * Default: a read-only HFS mount, refusing a dirty journal. An explicitly built
 * replay variant uses the ordinary HFS replay path, then remounts read-only.
 */
#ifdef LOG_RECOVERY_HOST_TEST
#include "log_recover_host.h"
#else
#include "mini.h"
#endif

#ifndef LOG_RECOVERY_NAME
#define LOG_RECOVERY_NAME ""
#endif
#ifndef LOG_RECOVERY_REPLAY
#define LOG_RECOVERY_REPLAY 0
#endif
#ifndef MNT_UPDATE
#define MNT_UPDATE 0x00010000
#endif
#ifndef O_NOFOLLOW
#define O_NOFOLLOW 0x00000100
#endif
#ifndef LOG_RECOVERY_HOST_TEST
#define MTIME_SEC(s) ((s).st_mtimespec.tv_sec)
#define MTIME_NSEC(s) ((s).st_mtimespec.tv_nsec)
#endif

#define FILE_CAP (2u * 1024u * 1024u)
#define CHUNK 768u
#define MAX_ENTRIES 4096u
static unsigned char contents[FILE_CAP];
static int did_replay;
struct recovery_dirent {
    uint64_t ino, seekoff;
    uint16_t reclen, namlen;
    uint8_t type;
    char name[1024];
};

static int
safe_name(const char *name)
{
    if (strlen(name) != 19 || (strncmp(name, "hakios-audio-", 13) &&
        strncmp(name, "hakios-debug-", 13))) return 0;
    for (unsigned i = 13; i < 19; i++)
        if (!((name[i] >= 'a' && name[i] <= 'z') || (name[i] >= 'A' && name[i] <= 'Z') ||
              (name[i] >= '0' && name[i] <= '9'))) return 0;
    return 1;
}

static uint32_t
crc_update(uint32_t crc, const unsigned char *bytes, size_t n)
{
    while (n--) {
        crc ^= *bytes++;
        for (unsigned b = 0; b < 8; b++) crc = (crc >> 1) ^ (0xedb88320u & (0u - (crc & 1u)));
    }
    return crc;
}

static char *text(char *out, const char *in) { while (*in) *out++ = *in++; return out; }
static char *number(char *out, uint64_t n)
{
    char digits[24]; unsigned count = 0;
    do { digits[count++] = (char)('0' + n % 10); n /= 10; } while (n);
    while (count) *out++ = digits[--count];
    return out;
}
static char *hex32(char *out, uint32_t n)
{
    static const char digits[] = "0123456789abcdef";
    for (int shift = 28; shift >= 0; shift -= 4) *out++ = digits[(n >> shift) & 15];
    return out;
}
static char *base64(char *out, const unsigned char *in, unsigned length)
{
    static const char alphabet[] = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    for (unsigned at = 0; at < length; at += 3) {
        unsigned left = length - at, value = (unsigned)in[at] << 16;
        if (left > 1) value |= (unsigned)in[at + 1] << 8;
        if (left > 2) value |= in[at + 2];
        *out++ = alphabet[value >> 18]; *out++ = alphabet[(value >> 12) & 63];
        *out++ = left > 1 ? alphabet[(value >> 6) & 63] : '=';
        *out++ = left > 2 ? alphabet[value & 63] : '=';
    }
    return out;
}
static int send_line(char *line, char *end)
{
    *end++ = '\n'; size_t left = (size_t)(end - line);
    while (left) {
        ssize_t n = write(1, line, left);
        if (n < 0 && errno == 4 /* EINTR */) continue;
        if (n <= 0) return -1;
        left -= (size_t)n; line += n;
    }
    return 0;
}

static int
same_file(const struct stat *a, const struct stat *b)
{
    return a->st_dev == b->st_dev && a->st_ino == b->st_ino && a->st_size == b->st_size &&
        MTIME_SEC(*a) == MTIME_SEC(*b) && MTIME_NSEC(*a) == MTIME_NSEC(*b) &&
        (b->st_mode & S_IFMT) == S_IFREG;
}

/* Only exact diagnostic names, regular files, a bounded directory and one file. */
static int
read_capture(const char *directory, const char *exact, char selected[20], unsigned *length)
{
    char entries[8192], path[512]; off_t position = 0;
    struct stat best, current, opened; unsigned seen = 0; int have = 0;
    if (*exact && !safe_name(exact)) return -1;
    size_t parent = strlen(directory);
    if (parent + 21 > sizeof(path)) return -1;
    int dir = open(directory, O_RDONLY | O_DIRECTORY | O_NOFOLLOW, 0);
    if (dir < 0) return -1;
    for (;;) {
        ssize_t count = getdirentries(dir, entries, sizeof(entries), &position);
        if (count < 0) { close(dir); return -1; }
        if (!count) break;
        for (size_t at = 0; at < (size_t)count;) {
            struct recovery_dirent decoded, *entry = &decoded;
            size_t header = offsetof(struct recovery_dirent, name);
            if ((size_t)count - at < header) { close(dir); return -1; }
            memcpy(entry, entries + at, header);
            if (!entry->reclen || entry->reclen > (size_t)count - at ||
                entry->namlen >= sizeof(entry->name) || entry->reclen < header + entry->namlen + 1 ||
                ++seen > MAX_ENTRIES) { close(dir); return -1; }
            memcpy(entry->name, entries + at + header, entry->namlen + 1);
            if (entry->name[entry->namlen] || strlen(entry->name) != entry->namlen) { close(dir); return -1; }
            at += entry->reclen;
            if (!safe_name(entry->name) || (*exact && strcmp(entry->name, exact))) continue;
            strcpy(path, directory); path[parent] = '/'; strcpy(path + parent + 1, entry->name);
            if (lstat(path, &current) || (current.st_mode & S_IFMT) != S_IFREG ||
                current.st_size <= 0 || current.st_size > FILE_CAP) continue;
            if (!have || MTIME_SEC(current) > MTIME_SEC(best) ||
                (MTIME_SEC(current) == MTIME_SEC(best) && MTIME_NSEC(current) > MTIME_NSEC(best)) ||
                (MTIME_SEC(current) == MTIME_SEC(best) && MTIME_NSEC(current) == MTIME_NSEC(best) &&
                 strcmp(entry->name, selected) > 0)) {
                best = current; strcpy(selected, entry->name); have = 1;
            }
        }
    }
    close(dir);
    if (!have) return -1;
    strcpy(path, directory); path[parent] = '/'; strcpy(path + parent + 1, selected);
    int fd = open(path, O_RDONLY | O_NOFOLLOW, 0);
    if (fd < 0) return -1;
    if (fstat(fd, &opened) || !same_file(&best, &opened)) { close(fd); return -1; }
    unsigned got = 0;
    while (got < (unsigned)best.st_size) {
        ssize_t n = read(fd, contents + got, (unsigned)best.st_size - got);
        if (n < 0 && errno == 4) continue;
        if (n <= 0) { close(fd); return -1; }
        got += (unsigned)n;
    }
    int unchanged = !fstat(fd, &opened) && same_file(&best, &opened);
    close(fd);
    if (!unchanged) return -1;
    *length = got; return 0;
}

static int
emit_round(const char *name, unsigned length, unsigned pace_us)
{
    char line[1200], *out; unsigned chunks = (length + CHUNK - 1) / CHUNK;
    uint32_t checksum = crc_update(~0u, contents, length) ^ ~0u;
    uint32_t id = crc_update(crc_update(~0u, (const unsigned char *)name, strlen(name)), contents, length) ^ ~0u;
    out = text(line, "LR1 BEGIN "); out = hex32(out, id); *out++ = ' '; out = text(out, name);
    *out++ = ' '; out = number(out, length); *out++ = ' '; out = number(out, chunks);
    *out++ = ' '; out = hex32(out, checksum); out = text(out, did_replay ? " replayed" : " readonly");
    if (send_line(line, out)) return -1;
    for (unsigned seq = 0, offset = 0; offset < length; seq++, offset += CHUNK) {
        unsigned bytes = length - offset < CHUNK ? length - offset : CHUNK;
        out = text(line, "LR1 DATA "); out = hex32(out, id); *out++ = ' '; out = number(out, seq);
        *out++ = ' '; out = number(out, offset); *out++ = ' '; out = number(out, bytes);
        *out++ = ' '; out = hex32(out, crc_update(~0u, contents + offset, bytes) ^ ~0u); *out++ = ' ';
        out = base64(out, contents + offset, bytes);
        if (send_line(line, out)) return -1;
        if (pace_us) usleep(pace_us);
    }
    out = text(line, "LR1 END "); out = hex32(out, id); *out++ = ' '; out = number(out, length);
    *out++ = ' '; out = number(out, chunks); *out++ = ' '; out = hex32(out, checksum);
    return send_line(line, out);
}

static int
mount_capture_volume(void)
{
    char arguments[1024], dev[80]; size_t size = sizeof(arguments) - 1;
    const char *value = NULL; size_t length = 0;
    if (sysctlbyname("kern.bootargs", arguments, &size, NULL, 0)) return -1;
    arguments[size < sizeof(arguments) ? size : sizeof(arguments) - 1] = 0;
    for (char *at = arguments; *at;) {
        while (*at == ' ') at++;
        char *end = at; while (*end && *end != ' ') end++;
        if (end - at > 8 && !strncmp(at, "var_hfs=", 8)) {
            if (value) return -1;
            value = at + 8; length = (size_t)(end - value);
        }
        at = end;
    }
    if (!value || !length || length + 6 > sizeof(dev)) return -1;
    for (size_t i = 0; i < length; i++)
        if (!((value[i] >= 'a' && value[i] <= 'z') || (value[i] >= '0' && value[i] <= '9') || value[i] == '_')) return -1;
    strcpy(dev, "/dev/"); memcpy(dev + 5, value, length); dev[5 + length] = 0;
    struct { const char *fspec; uint32_t uid, gid; uint16_t mask, pad; uint32_t encoding;
        int tz[2], flags, journal_tbuffer_size, journal_flags, journal_disable; } args;
    memset(&args, 0, sizeof(args)); args.fspec = dev;
    if (!mount("hfs", "/private/var", MNT_RDONLY, &args)) return 0;
    printf("LR1 ERROR read-only HFS mount refused errno=%d; a dirty journal requires the explicit replay image\n", errno);
#if LOG_RECOVERY_REPLAY
    if (mount("hfs", "/private/var", 0, &args)) return -1;
    if (mount("hfs", "/private/var", MNT_UPDATE | MNT_RDONLY, &args)) return -1;
    did_replay = 1;
    return 0;
#else
    return -1;
#endif
}

#ifndef LOG_RECOVERY_NO_MAIN
int main(int argc, char **argv, char **envp)
{
    (void)argc; (void)argv; (void)envp;
    int fd = open("/dev/console", O_RDWR, 0);
    if (fd >= 0) { dup2(fd, 0); dup2(fd, 1); dup2(fd, 2); if (fd > 2) close(fd); }
    printf("LR1 READY private audio recovery; hardware services off; mount policy %s\n",
           LOG_RECOVERY_REPLAY ? "explicit replay then read-only" : "read-only");
    char name[20] = ""; unsigned length = 0;
    if (!mount_capture_volume() && !read_capture("/private/var/log", LOG_RECOVERY_NAME, name, &length)) {
        usleep(2000000);
        for (;;) { if (emit_round(name, length, 25000)) break; usleep(3000000); }
    } else {
        printf("LR1 ERROR capture unavailable; exact-name selection never falls back to an older file\n");
    }
    /* Stay as PID1 so the live loader remains available. Host ends the run. */
    for (;;) usleep(1000000);
}
#endif
