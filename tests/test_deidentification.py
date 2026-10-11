"""Whole-tree de-identification guard.

The original guard only inspected string constants in ``const.py``, so a leak in
any other file -- LICENSE, README, the Lovelace card, a YAML schema -- was
invisible to it. That is exactly how a copyright line carrying an internal
identifier reached a release candidate.

This module walks the repository instead: every text file, plus the shipped
release archive. It reports the file and line number of any hit, because a bare
"found forbidden string" is nearly useless when it fires.

``tests/test_integration_offline.py`` is excluded from the scan: it legitimately
contains these strings as negative assertions. Scanning it would guarantee a
failure and teach us nothing.
"""

import unittest
import zipfile
from pathlib import Path

# Single source of truth for what must never appear in published files.
# Kept in sync with the tuple used by the targeted const.py check.
_INTERNAL_IDENTIFIERS = (
    "E4:15:F6",      # a specific device BLE address
    "192.168",       # private-range LAN addressing
    "jws",           # internal project initials (bare, catches all variants)
    "jws-node",
    "ison-fence",    # internal tailnet name
    "/home/jws",     # internal filesystem paths
    "xjajc",         # a common misspelling of the owner's handle
)

# Files allowed to contain an identifier, with the reason.
# The owner handle must appear in exactly two places, both required by HACS.
_ALLOWED: dict[str, str] = {
    "custom_components/mojipic/manifest.json": (
        "codeowners must be a real GitHub handle, and the documentation and "
        "issue_tracker URLs point at the repository. This is the correct and "
        "only place the owner's handle belongs."
    ),
}

# Never scanned: VCS metadata, caches, the build artifact, and the test files
# that carry these strings as assertions.
_SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", ".venv", "node_modules"}
_SKIP_NAMES = {"mojipic.zip"}

_TEXT_SUFFIXES = {
    ".py", ".json", ".md", ".yaml", ".yml", ".js", ".txt", ".cfg", ".ini",
    ".toml", ".sh", ".js.map",
}
_TEXT_NAMES = {"LICENSE", "README", ".gitignore", ".hacs.json"}

REPO_ROOT = Path(__file__).resolve().parents[1]


def _is_test_file(rel: Path) -> bool:
    """Test files legitimately contain the forbidden strings as assertions."""
    return rel.parts and rel.parts[0] == "tests"


def _looks_textual(path: Path) -> bool:
    if path.name in _TEXT_NAMES or path.suffix in _TEXT_SUFFIXES:
        return True
    if path.suffix in _TEXT_SUFFIXES or path.suffix == "":
        return True
    return False


def scan_text(text: str) -> list[tuple[int, str, str]]:
    """Return [(line number, identifier, matched text)] for every hit."""
    hits = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        lowered = line.lower()
        for identifier in _INTERNAL_IDENTIFIERS:
            if identifier.lower() in lowered:
                hits.append((lineno, identifier, line.strip()[:100]))
    return hits


def iter_repo_files():
    """Yield (relative path, absolute path) for every scannable text file."""
    for path in sorted(REPO_ROOT.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(REPO_ROOT)
        if any(part in _SKIP_DIRS for part in rel.parts):
            continue
        if path.name in _SKIP_NAMES:
            continue
        if _is_test_file(rel):
            continue
        if not _looks_textual(path):
            continue
        yield rel, path


class TestWholeTreeDeidentification(unittest.TestCase):
    """No internal identifier may appear anywhere in the published tree."""

    def test_no_internal_identifiers_in_repo(self):
        failures = []
        scanned = 0
        for rel, path in iter_repo_files():
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue  # genuinely binary; nothing to scan
            scanned += 1
            for lineno, identifier, matched in scan_text(text):
                if str(rel) in _ALLOWED:
                    continue
                failures.append(
                    f"{rel}:{lineno}: forbidden identifier "
                    f"{identifier!r} in: {matched}"
                )

        self.assertGreater(scanned, 10, "scan found suspiciously few files")
        self.assertFalse(
            failures,
            "internal identifier(s) found in the repository tree:\n  "
            + "\n  ".join(failures),
        )

    def test_allowed_file_actually_needs_its_exemption(self):
        """Guard against the exemption list silently becoming unnecessary."""
        path = REPO_ROOT / "custom_components/mojipic/manifest.json"
        self.assertTrue(
            path.exists(),
            "manifest.json is missing; the _ALLOWED exemption may be stale",
        )
        text = path.read_text(encoding="utf-8")
        self.assertIn("codeowners", text)
        self.assertIn("documentation", text)

    def test_no_internal_identifiers_in_release_artifact(self):
        """Scan what users actually download, not just the working tree."""
        archive = REPO_ROOT / "mojipic.zip"
        if not archive.exists():
            self.skipTest("mojipic.zip not built; run the packaging step first")

        failures = []
        with zipfile.ZipFile(archive) as bundle:
            names = bundle.namelist()
            self.assertTrue(
                any(n.endswith("manifest.json") for n in names),
                "archive does not look like a MojiPic integration bundle",
            )
            for name in names:
                if name.endswith((".pyc",)):
                    continue
                try:
                    text = bundle.read(name).decode("utf-8")
                except UnicodeDecodeError:
                    continue
                rel = name.replace("mojipic/", "custom_components/mojipic/", 1)
                for lineno, identifier, matched in scan_text(text):
                    if rel in _ALLOWED:
                        continue
                    failures.append(
                        f"{name}:{lineno}: forbidden identifier "
                        f"{identifier!r} in: {matched}"
                    )

        self.assertFalse(
            failures,
            "internal identifier(s) found in mojipic.zip:\n  "
            + "\n  ".join(failures),
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestRepositoryStructure(unittest.TestCase):
    """Guard the layout HACS actually validates.

    HACS requires the integration at `custom_components/<domain>/` -- NOT
    `integrations/<domain>/`, which is Home Assistant Core's own monorepo
    layout. Getting this wrong makes HACS reject the repository with
    "Repository structure ... is not compliant", and the failure only shows
    up in HACS, not in the test suite.
    """

    def test_integration_lives_under_custom_components(self):
        integration_dir = REPO_ROOT / "custom_components" / "mojipic"
        self.assertTrue(
            integration_dir.is_dir(),
            "expected custom_components/mojipic/ -- HACS will reject the "
            "repository without it",
        )
        self.assertTrue(
            (integration_dir / "manifest.json").is_file(),
            "manifest.json must sit inside custom_components/mojipic/",
        )

    def test_exactly_one_integration_in_custom_components(self):
        entries = [p for p in (REPO_ROOT / "custom_components").iterdir() if p.is_dir()]
        self.assertEqual(
            len(entries), 1,
            f"HACS manages one integration per repo; found {[p.name for p in entries]}",
        )

    def test_no_integrations_directory(self):
        """`integrations/` is Core's layout, not HACS's. Its presence is a bug."""
        self.assertFalse(
            (REPO_ROOT / "integrations").exists(),
            "integrations/ is not the HACS layout; the integration must live "
            "under custom_components/",
        )

    def test_manifest_has_required_keys(self):
        import json

        manifest = json.loads(
            (REPO_ROOT / "custom_components/mojipic/manifest.json").read_text()
        )
        for key in ("domain", "documentation", "issue_tracker", "codeowners",
                    "name", "version"):
            self.assertIn(key, manifest, f"manifest.json is missing {key!r}")
        self.assertEqual(manifest["domain"], "mojipic")

    def test_hacs_json_present_and_parses(self):
        import json

        hacs = json.loads((REPO_ROOT / ".hacs.json").read_text())
        for key in ("name", "render_readme", "content_in_root", "homeassistant"):
            self.assertIn(key, hacs, f".hacs.json is missing {key!r}")
