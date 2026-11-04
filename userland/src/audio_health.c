/* Optional diagnostic: bounded, private audio/kernel evidence on /private/var.
 * No mixer, driver, network, power, UI or charging controls are changed.
 * Run from a diagnostic launchd/rc.jobs profile; it exits after <=180 seconds.
 */
#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/sysctl.h>
#include <sys/time.h>
#include <unistd.h>
#define CAPTURE_MAX (2u*1024u*1024u)
#define SNAPSHOT_MAX (1024u*1024u)
/* XNU8796 libsyscall/wrappers/libproc/libproc.h; exported by iOS16.5
 * libSystem. kern.msgbuf is an integer size control, not the log text. */
extern int proc_kmsgbuf(void *buffer,uint32_t buffersize);
static size_t total;
static char*previous;
static size_t previous_size;
static int write_all(int fd,const void*data,size_t n)
{
    const char*p=data;
    if(n>CAPTURE_MAX-total)return -1;
    while(n) { ssize_t k=write(fd,p,n);if(k<0 && errno==EINTR)continue;if(k<=0)return -1;p+=k;n-=k;total+=(size_t)k; }
    return 0;
}
/* Longest whole-line suffix of the previous snapshot that is the prefix of
 * this one. KMP keeps ring-wrap/repeated-line handling linear and bounded. */
static size_t matching_prefix(const char*old,size_t old_size,const char*next,size_t next_size)
{
    if(!old_size || !next_size)return 0;
    uint32_t*prefix=malloc(next_size*sizeof *prefix);if(!prefix)return 0;
    prefix[0]=0;
    for(size_t i=1,matched=0;i<next_size;i++) {
        while(matched && next[i]!=next[matched])matched=prefix[matched-1];
        if(next[i]==next[matched])matched++;
        prefix[i]=(uint32_t)matched;
    }
    size_t matched=0;
    for(size_t i=0;i<old_size;i++) {
        while(matched && (matched==next_size || old[i]!=next[matched]))matched=prefix[matched-1];
        if(matched<next_size && old[i]==next[matched])matched++;
    }
    while(matched && (next[matched-1]!='\n' || (old_size>matched && old[old_size-matched-1]!='\n')))
        matched=prefix[matched-1];
    free(prefix);return matched;
}
static int snapshot(int fd,unsigned second)
{
    char*raw=malloc(SNAPSHOT_MAX+1),*filtered=malloc(SNAPSHOT_MAX+2);
    if(!raw||!filtered){free(raw);free(filtered);return -1;}
    errno=0;int result=proc_kmsgbuf(raw,SNAPSHOT_MAX);
    if(result<=0 || result>(int)SNAPSHOT_MAX) {
        int failure=errno;char line[160];
        /* Include immediate/periodic evidence even if the phone restarts
         * before the capture-complete footer can be written. */
        if(second<3 || second%10==0) {
            int k=snprintf(line,sizeof line,"health +%us proc_kmsgbuf failed: result=%d errno=%d\n",second,result,failure);
            if(k>0)(void)write_all(fd,line,(size_t)k);
        }
        free(raw);free(filtered);return -1;
    }
    size_t n=(size_t)result;
    /* libproc includes a trailing NUL; bounds also cover an exact-size
     * truncated snapshot or a test provider with no terminating NUL. */
    raw[n]=0;n=strnlen(raw,n);
    raw[n]=0;size_t used=0;
    for(size_t at=0;at<n;) {
        size_t end=at;while(end<n && raw[end]!='\n')end++;
        char saved=raw[end];raw[end]=0;
        if(strstr(raw+at,"sound:") || strstr(raw+at,"speaker:") || strstr(raw+at,"adsp:") || strstr(raw+at,"haptic:") ||
            strstr(raw+at,"hakios: audio:")) {
            size_t bytes=end-at;if(bytes){memcpy(filtered+used,raw+at,bytes);used+=bytes;filtered[used++]='\n';}
        }
        raw[end]=saved;at=end<n?end+1:n;
    }
    size_t overlap=matching_prefix(previous,previous_size,filtered,used);int rc=0;
    if(used>overlap) {
        char heading[100];struct timeval t;gettimeofday(&t,NULL);
        int k=snprintf(heading,sizeof heading,"\n== audio kernel snapshot +%us utc=%lld ==\n",second,(long long)t.tv_sec);
        if(k>0 && write_all(fd,heading,(size_t)k)==0)rc=write_all(fd,filtered+overlap,used-overlap);else rc=-1;
    }
    free(raw);free(previous);previous=filtered;previous_size=used;return rc;
}
static void battery_snapshot(int fd,unsigned second)
{
    const char*names[]={"kern.hakios.battery_soc","kern.hakios.battery_present","kern.hakios.battery_charging"};
    for(unsigned i=0;i<sizeof names/sizeof names[0];i++) {
        int v=0;size_t n=sizeof v;char line[160];
        if(sysctlbyname(names[i],&v,&n,NULL,0)!=0 || n!=sizeof v)continue;
        int k=snprintf(line,sizeof line,"health +%us %s=%d\n",second,names[i],v);
        if(k>0)(void)write_all(fd,line,(size_t)k);
    }
}
static void process_snapshot(int fd,unsigned second)
{
    int mib[4]={CTL_KERN,KERN_PROC,KERN_PROC_ALL,0};size_t bytes=0;
    if(sysctl(mib,4,NULL,&bytes,NULL,0)!=0 || !bytes || bytes>SNAPSHOT_MAX)return;
    struct kinfo_proc*processes=malloc(bytes);if(!processes)return;
    if(sysctl(mib,4,processes,&bytes,NULL,0)!=0){free(processes);return;}
    char line[160];int k=snprintf(line,sizeof line,"== audio process health +%us ==\n",second);
    if(k>0)(void)write_all(fd,line,(size_t)k);
    for(size_t i=0;i<bytes/sizeof *processes;i++) {
        struct extern_proc*p=&processes[i].kp_proc;
        if(!strncmp(p->p_comm,"mediaserverd",sizeof p->p_comm) || !strncmp(p->p_comm,"coreaudiod",sizeof p->p_comm) ||
            !strncmp(p->p_comm,"SpringBoard",sizeof p->p_comm) || !strncmp(p->p_comm,"powerd",sizeof p->p_comm)) {
            k=snprintf(line,sizeof line,"process pid=%d status=%d name=%.*s\n",p->p_pid,p->p_stat,(int)sizeof p->p_comm,p->p_comm);
            if(k>0)(void)write_all(fd,line,(size_t)k);
        }
    }
    free(processes);
}
int main(int argc,char**argv)
{
    unsigned seconds=90;if(argc>2)return 2;
    if(argc==2) { char*end;unsigned long n=strtoul(argv[1],&end,10);if(!*argv[1] || *end || n<1 || n>180)return 2;seconds=(unsigned)n; }
    char path[]="/private/var/log/hakios-audio-XXXXXX";
    int fd=mkstemp(path);if(fd<0){perror("audio_health log");return 1;}
    (void)fchmod(fd,0600);printf("audio_health: private bounded capture %s for %us uid=%u euid=%u\n",
        path,seconds,(unsigned)getuid(),(unsigned)geteuid());fflush(stdout);
    const char*start="Audio diagnostic only: kernel sound/speaker/adsp/haptic lines, battery and named audio/UI services.\n";
    if(write_all(fd,start,strlen(start))!=0){close(fd);return 1;}
    char identity[160];int identity_size=snprintf(identity,sizeof identity,"capture identity: pid=%d uid=%u euid=%u API=proc_kmsgbuf\n",
        getpid(),(unsigned)getuid(),(unsigned)geteuid());
    if(identity_size>0 && write_all(fd,identity,(size_t)identity_size)!=0){close(fd);return 1;}
    unsigned errors=0;
    for(unsigned second=0;second<=seconds && total<CAPTURE_MAX;second++) {
        if(snapshot(fd,second)!=0)errors++;
        battery_snapshot(fd,second);process_snapshot(fd,second);
        if(fsync(fd)!=0)break;
        if(second<seconds)sleep(1);
    }
    char end[100];int k=snprintf(end,sizeof end,"capture complete: bytes=%zu snapshot_errors=%u\n",total,errors);
    if(k>0)(void)write_all(fd,end,(size_t)k);(void)fsync(fd);close(fd);free(previous);
    return errors>seconds?1:0;
}
