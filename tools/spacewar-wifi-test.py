#!/usr/bin/env python3
"""Host-test actual Phone (1) Wi-Fi policy and private firmware preparation."""
from pathlib import Path
import importlib.util
import os
import shutil
import struct
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("fw", ROOT / "tools/spacewar-wifi-firmware.py")
fw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fw)


def board():
    b = bytearray(256)
    b[:7] = b"\x7fELF\x01\x01\x01"
    struct.pack_into("<HH", b, 16, 2, 40)
    struct.pack_into("<I", b, 28, 52)
    struct.pack_into("<HHH", b, 40, 52, 32, 1)
    struct.pack_into("<8I", b, 52, 1, 128, 4096, 4096, 64, 64, 6, 32)
    return bytes(b)


def fixture(path):
    image = path / "image"
    (image / "qca6750").mkdir(parents=True)
    head = bytearray(52 + 3 * 32 + 64)
    head[:7] = b"\x7fELF\x01\x01\x01"
    struct.pack_into("<I", head, 28, 52)
    struct.pack_into("<HHH", head, 40, 52, 32, 3)
    struct.pack_into("<8I", head, 52 + 32, 0, 0, 0, 0, 64, 64, 2 << 24, 0)
    struct.pack_into("<8I", head, 52 + 64, 1, 0, 0, fw.WPSS_BASE, 128, 256, 5, 0)
    (image / "wpss.mdt").write_bytes(head)
    (image / "wpss.b02").write_bytes(bytes(128))
    (image / "qca6750/bdwlan.elf").write_bytes(board())
    (image / "qca6750/regdb.bin").write_bytes(b"regdb")
    return image


class WifiTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)

    def test_actual_c_policy_under_sanitizers(self):
        c = r'''
#include <assert.h>
#include <string.h>
#include "drivers/wifi/hakios.spacewar.wifi.h"
#include "wifi/lx/src/lx_security.h"
int main(void) {
 /* Actual association wire rules: open BSSs cannot acquire Privacy or RSN;
  * requesting open never downgrades any advertised protection. */
 for(unsigned cap=0;cap<=65535;cap++) {
  assert(lx_open_ap((uint16_t)cap,false,false)==!(cap&0x10));
  assert(!lx_open_ap((uint16_t)cap,true,false));
  assert(!lx_open_ap((uint16_t)cap,false,true));
  assert(!lx_open_ap((uint16_t)cap,true,true));
  assert(!(lx_assoc_capability((uint16_t)cap,false)&0x10));
  assert(lx_assoc_capability((uint16_t)cap,true)&0x10);
  assert((lx_assoc_capability((uint16_t)cap,false)&0x0520)==(cap&0x0520));
 }
 uint8_t rsn[24];memset(rsn,0xa5,sizeof(rsn));
 assert(lx_assoc_rsn(rsn,0)==0);
 for(unsigned i=0;i<22;i++)assert(rsn[i]==0);
 assert(rsn[22]==0xa5&&rsn[23]==0xa5);
 assert(lx_assoc_rsn(rsn,4)==22);
 assert(rsn[0]==48&&rsn[1]==20&&rsn[7]==4&&rsn[13]==4&&rsn[19]==2);
 assert(rsn[22]==0xa5&&rsn[23]==0xa5);
 /* Measured warm-start regression: the old word is0x10e and only the
  * sibling wlan entry changes. It must not begin a new zero-duration
  * ready watch or let ath11k start before this generation says ready. */
 struct spacewar_wifi_start_state start=spacewar_wifi_start_begin(true,0x10e,0);
 assert(!spacewar_wifi_start_observe(&start,0x10e,0));
 assert(!start.fresh);
 assert(!spacewar_wifi_start_observe(&start,0,0));
 assert(spacewar_wifi_start_observe(&start,6,0));
 assert(!spacewar_wifi_start_observe(&start,0x10e,0));
 assert(!spacewar_wifi_start_observe(&start,7,0));
 /* Stale ready without stop-ack also needs a new state or restart edge. */
 start=spacewar_wifi_start_begin(true,6,0);
 assert(!spacewar_wifi_start_observe(&start,6,0));
 assert(spacewar_wifi_start_observe(&start,6,1));
 start=spacewar_wifi_start_begin(true,6,1);
 assert(spacewar_wifi_start_observe(&start,6,0));
 /* Fresh cold starts and all combinations of fatal/ready/stop bits. */
 for(unsigned state=0;state<512;state++) {
  start=spacewar_wifi_start_begin(false,0,0);
  bool expected=(state&2)&&!(state&1)&&!(state&8);
  assert(spacewar_wifi_start_observe(&start,state,0)==expected);
  start=spacewar_wifi_start_begin(true,state,0);
  assert(!spacewar_wifi_start_observe(&start,state,0));
 }
 assert(!strcmp(spacewar_wifi_alias(true,"board.bin"),"wifi-board.elf"));
 assert(!strcmp(spacewar_wifi_alias(true,"regdb.bin"),"wifi-regdb.bin"));
 assert(!strcmp(spacewar_wifi_alias(false,"board.bin"),"bd_m20gl.elf"));
 assert(!strcmp(spacewar_wifi_alias(false,"regdb.bin"),"regdb_xiaomi.bin"));
 assert(!spacewar_wifi_alias(true,"board-2.bin"));
 assert(!spacewar_wifi_alias(true,"bd_m20gl.elf"));
 unsigned char a[6], old[6]; memset(a,0xa5,6);memcpy(old,a,6);
 const char *bad[]={"","00:00:00:00:00:00","01:11:22:33:44:55","ff:ff:ff:ff:ff:ff",
 "2:11:22:33:44:55","02:11:22:33:44:5z","02:11:22:33:44:55more","02-11-22-33-44-55"};
 for (unsigned i=0;i<sizeof(bad)/sizeof(*bad);i++) {
  assert(!spacewar_wifi_mac((const uint8_t*)bad[i],strlen(bad[i]),a));assert(!memcmp(a,old,6));
 }
 assert(!spacewar_wifi_mac(NULL,17,a));
 assert(spacewar_wifi_mac((const uint8_t*)"02:11:22:aa:BB:55",17,a));
 assert(a[0]==2&&a[3]==0xaa&&a[4]==0xbb);
 assert(spacewar_wifi_mac((const uint8_t*)"02:11:22:aa:BB:55\n",18,a));
 assert(spacewar_wifi_mac((const uint8_t*)"02:11:22:aa:BB:55\r\n",19,a));
 assert(!spacewar_wifi_board(NULL,0));
 uint8_t b[256]={0};memcpy(b,"\177ELF\1\1\1",7);b[16]=2;b[18]=40;b[28]=52;b[40]=52;b[42]=32;b[44]=1;
 b[52]=1;b[56]=128;b[68]=64;b[72]=64;
 assert(spacewar_wifi_board(b,sizeof(b)));
 assert(!spacewar_wifi_board(b,191));
 b[68]=65;assert(!spacewar_wifi_board(b,sizeof(b)));b[68]=64;
 b[28]=255;assert(!spacewar_wifi_board(b,sizeof(b)));b[28]=52;
 b[44]=0;assert(!spacewar_wifi_board(b,sizeof(b)));b[44]=17;assert(!spacewar_wifi_board(b,sizeof(b)));
 return 0;
}
'''
        source, binary = self.path / "policy.c", self.path / "policy"
        source.write_text(c)
        subprocess.run([shutil.which("clang") or "cc", "-std=c11", "-Wall", "-Wextra", "-Werror",
                        "-fsanitize=address,undefined", "-I", str(ROOT), str(source), "-o", str(binary)], check=True)
        subprocess.run([str(binary)], check=True, env={**os.environ, "ASAN_OPTIONS": "detect_leaks=0"})

    def test_preparation_keeps_private_mac_out_of_boot_args_and_manifest(self):
        image = fixture(self.path)
        mac = self.path / "address.private"
        mac.write_text("Intf0MacAddress=021122334455\nIntf1MacAddress=021122334456\nEND\n")
        out = self.path / "ready"
        result = fw.prepare(image, "bdwlan.elf", mac, out)
        self.assertTrue(result["has_station_address"])
        self.assertEqual((out / "mac").read_bytes(), b"02:11:22:33:44:55\n")
        self.assertNotIn("02:11:22:33:44:55", (out / "manifest.json").read_text())
        self.assertEqual(os.stat(out / "mac").st_mode & 0o777, 0o600)
        self.assertEqual((out / "qca6750/wifi-board.elf").read_bytes(), board())
        with self.assertRaisesRegex(ValueError, "already exists"):
            fw.prepare(image, "bdwlan.elf", mac, out)

    def test_signed_image_wrong_lengths_bounds_or_overlap_are_rejected(self):
        image = fixture(self.path)
        head = (image / "wpss.mdt").read_bytes()
        (image / "wpss.b02").write_bytes(bytes(127))
        with self.assertRaisesRegex(ValueError, "length mismatch"):
            fw.validate_wpss(image)
        (image / "wpss.b02").write_bytes(bytes(128))
        for offset, value in ((52 + 64 + 12, fw.WPSS_BASE - 1), (52 + 64 + 20, fw.WPSS_BYTES + 1),
                              (52 + 32 + 16, 65), (28, 0)):
            b = bytearray(head)
            struct.pack_into("<I", b, offset, value)
            (image / "wpss.mdt").write_bytes(b)
            with self.assertRaises(ValueError):
                fw.validate_wpss(image)
        (image / "wpss.mdt").write_bytes(head)

    def test_board_or_mac_rejection_has_no_output(self):
        image = fixture(self.path)
        out = self.path / "bad-output"
        for name in ("../bdwlan.elf", "bd_m20gl.elf", "missing.elf"):
            with self.assertRaises((ValueError, OSError)):
                fw.prepare(image, name, None, out)
            self.assertFalse(out.exists())
        (image / "qca6750/bdwlan.elf").write_bytes(b"not ELF")
        with self.assertRaises(ValueError):
            fw.prepare(image, "bdwlan.elf", None, out)
        self.assertFalse(out.exists())
        for data in (b"00:00:00:00:00:00", b"01:11:22:33:44:55", b"bad", b"Intf0MacAddress=021122334455\nIntf0MacAddress=021122334455"):
            with self.assertRaises(ValueError):
                fw.station_address(data)


if __name__ == "__main__":
    unittest.main(verbosity=2)
