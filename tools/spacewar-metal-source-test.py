#!/usr/bin/env python3
"""Exercise native Metal source acceptance in temporary Git repositories."""
import hashlib
import importlib.util
import io
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("metal_builder", ROOT / "tools/build-spacewar-metal.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)

CACHE_PATCHES = {
    "compiler-cache-hits.patch",
    "compiler-cache-hits-test.patch",
    "compiler-cache-locks.patch",
}
CACHE_HEADER = "include/hakios-metal/compiler_cache.hpp"


def git(folder, *args, binary=False):
    return subprocess.check_output(
        ["git", "-c", "core.autocrlf=false", "-c", "core.filemode=true",
         "-c", "core.hooksPath=/dev/null", *args],
        cwd=folder, text=not binary, stderr=subprocess.PIPE)


class MetalSourceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory(prefix="spacewar-metal-source-test-")
        cls.addClassCleanup(cls.folder.cleanup)
        cls.fixture = Path(cls.folder.name) / "fixture"
        cls.fixture.mkdir()
        tree = git(ROOT, "rev-parse", builder.BUNDLED_REVISION + ":hakios-metal").strip()
        if tree != builder.BUNDLED_TREE:
            raise AssertionError("fixture must come from the unchanged reviewed tree pin")
        archive = git(ROOT, "archive", "--format=tar", "--prefix=hakios-metal/", tree, binary=True)
        with tarfile.open(fileobj=io.BytesIO(archive)) as stream:
            for member in stream:
                if member.isdir():
                    continue
                if not member.isfile():
                    raise AssertionError("unexpected non-file in pinned fixture: " + member.name)
                target = cls.fixture / member.name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(stream.extractfile(member).read())
                target.chmod(member.mode)
        git(cls.fixture, "init", "--quiet", "--template=")
        git(cls.fixture, "add", "--", "hakios-metal")
        git(cls.fixture, "-c", "user.name=Source policy fixture", "-c",
            "user.email=fixture@example.invalid", "-c", "commit.gpgsign=false",
            "commit", "--quiet", "-m", "Pinned Metal source fixture")
        cls.revision = git(cls.fixture, "rev-parse", "HEAD").strip()
        if git(cls.fixture, "rev-parse", "HEAD:hakios-metal").strip() != builder.BUNDLED_TREE:
            raise AssertionError("archive import changed the reviewed source tree")
        shutil.copytree(ROOT / "tools/metal", cls.fixture / "tools/metal")

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix="spacewar-metal-source-case-")
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name) / "repo"
        shutil.copytree(self.fixture, self.root)
        self.source = self.root / "hakios-metal"
        # Only redirect the repository and fixture commit. BUNDLED_TREE stays
        # exactly the production pin, including original file modes and bytes.
        overrides = patch.multiple(builder, ROOT=self.root, BUNDLED_REVISION=self.revision)
        overrides.start()
        self.addCleanup(overrides.stop)

    def inspect(self, enabled=True, feedback=False):
        return builder.inspect_source(self.source, sysmem_feedback_order=feedback,
                                      compiler_cache_hits=enabled)

    def apply(self, names):
        for name in names:
            git(self.root, "apply", "--directory=hakios-metal", self.root / "tools/metal" / name)

    def apply_pending(self, feedback=False):
        provenance, pending, _, _ = self.inspect(feedback=feedback)
        self.apply(pending)
        return provenance

    def test_original_tree_reports_new_header_pending_without_mutation(self):
        before = git(self.root, "status", "--porcelain", "--", "hakios-metal")
        index = (self.root / ".git/index").read_bytes()
        original = {p.relative_to(self.source): p.read_bytes()
                    for p in self.source.rglob("*") if p.is_file()}
        for feedback in (False, True):
            with self.subTest(feedback=feedback):
                provenance, pending, top, prefix = self.inspect(feedback=feedback)
                self.assertTrue(CACHE_PATCHES <= set(pending))
                self.assertEqual(set(pending), set(provenance["scoped_patch_files"]))
                self.assertIn(CACHE_HEADER, provenance["compiled_source_files"])
                self.assertTrue(provenance["compiler_cache_hits"])
                self.assertEqual(provenance["reviewed_tree"], builder.BUNDLED_TREE)
                self.assertEqual(top, self.root)
                self.assertEqual(prefix, "hakios-metal/")
                self.assertFalse((self.source / CACHE_HEADER).exists())
        self.assertEqual(original, {p.relative_to(self.source): p.read_bytes()
                                    for p in self.source.rglob("*") if p.is_file()})
        # Inspecting must not stage files or otherwise rewrite the index.
        self.assertEqual((self.root / ".git/index").read_bytes(), index)
        self.assertEqual(git(self.root, "status", "--porcelain", "--", "hakios-metal"), before)

    def test_all_scoped_patches_are_accepted_with_matching_hashes(self):
        expected = self.apply_pending(feedback=True)
        for staged in (False, True):
            with self.subTest(header_staged=staged):
                if staged:
                    git(self.root, "add", "--", "hakios-metal/" + CACHE_HEADER)
                provenance, pending, _, _ = self.inspect(feedback=True)
                self.assertEqual(pending, [])
                self.assertEqual(provenance["compiled_source_sha256"], expected["compiled_source_sha256"])
                for relative, digest in provenance["compiled_source_files"].items():
                    self.assertEqual(hashlib.sha256((self.source / relative).read_bytes()).hexdigest(), digest)

    def test_extra_source_is_refused_untracked_and_staged(self):
        self.apply_pending()
        relative = "tests/unreviewed_test.cpp"
        (self.source / relative).write_text("// Not part of the reviewed tree or scoped patches.\n")
        for staged in (False, True):
            with self.subTest(staged=staged):
                if staged:
                    git(self.root, "add", "--", "hakios-metal/" + relative)
                with self.assertRaisesRegex(ValueError, "source paths differ.*unreviewed_test"):
                    self.inspect()

    def test_approved_compiler_and_header_paths_require_exact_bytes(self):
        self.apply_pending()
        for relative in ("src/compiler.cpp", CACHE_HEADER):
            with self.subTest(relative=relative):
                target = self.source / relative
                accepted = target.read_bytes()
                target.write_bytes(accepted + b"\n// An unrelated change in an approved path.\n")
                try:
                    with self.assertRaisesRegex(ValueError, "source has unreviewed changes: " + relative):
                        self.inspect()
                finally:
                    target.write_bytes(accepted)

    def test_new_header_requires_enabled_flag(self):
        self.apply(["compiler-cache-locks.patch"])
        with self.assertRaisesRegex(ValueError, "source paths differ.*compiler_cache"):
            self.inspect(enabled=False)

    def test_compiler_change_requires_enabled_flag(self):
        self.apply(["compiler-cache-hits.patch"])
        with self.assertRaisesRegex(ValueError, "source has unreviewed changes: src/compiler.cpp"):
            self.inspect(enabled=False)

    def test_new_header_cannot_redirect_to_another_file(self):
        self.apply_pending()
        header = self.source / CACHE_HEADER
        other = self.root / "same-header-bytes.hpp"
        header.rename(other)
        header.symlink_to(other)
        with self.assertRaisesRegex(ValueError, "missing or redirected source file: " + CACHE_HEADER):
            self.inspect()

    def test_header_patch_cannot_authorize_a_second_source(self):
        patch_file = self.root / "tools/metal/compiler-cache-locks.patch"
        with patch_file.open("a") as stream:
            stream.write("\n--- /dev/null\n+++ b/src/unreviewed.cpp\n@@ -0,0 +1 @@\n+// Extra source.\n")
        with self.assertRaisesRegex(ValueError, "patch is not scoped.*compiler-cache-locks"):
            self.inspect()

    def test_padded_constants_preserve_feedback_order_and_accept_exact_sources(self):
        provenance, pending, _, _ = builder.inspect_source(self.source, True, True, True)
        self.assertTrue(provenance["padded_constants"])
        self.assertTrue(provenance["sysmem_feedback_order"])
        self.assertIn("padded-constants.patch", pending)
        self.apply(pending)
        _, pending, _, _ = builder.inspect_source(self.source, True, True, True)
        self.assertEqual(pending, [])
        target = self.source / "src/render.cpp"
        target.write_bytes(target.read_bytes() + b"\n// Unreviewed renderer edit.\n")
        with self.assertRaisesRegex(ValueError, "source has unreviewed changes: src/render.cpp"):
            builder.inspect_source(self.source, True, True, True)

    def test_padded_constants_require_feedback_order(self):
        with self.assertRaisesRegex(ValueError, "requires.*sysmem-feedback-order"):
            builder.inspect_source(self.source, False, True, True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
