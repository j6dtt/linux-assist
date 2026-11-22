"""Tests for the checker, run inside the built image against its real man page data:

  docker run --rm -v "$PWD/tests:/tests:ro" --entrypoint python la:1.0 -m unittest discover -s /tests -v
"""
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout

from la import check, cmdline, danger
from la.data import RELEASES, Release
from la.verify import check_line


def problems(rel, line):
    return [p for r in check_line(rel, line) for p in r.problems]


class FlagsAgainstManPages(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rels = [Release(r) for r in RELEASES]

    def test_real_commands_pass(self):
        for rel in self.rels:
            for line in ["ss -tulpn", "sudo du -xh --max-depth=1 /var | sort -h",
                         "find / -xdev -type f -size +500M", "ip -br addr",
                         "tar -czf a.tgz --exclude='*.log' /etc", "journalctl -u ssh -n 50 --no-pager",
                         "sudo lvextend -r -L +20G <VG>/<LV>",
                         "sudo iptables -A INPUT -p tcp --dport 22 -j ACCEPT"]:
                self.assertEqual(problems(rel, line), [], f"{line} on {rel.release}")

    def test_made_up_flag_is_caught(self):
        for rel in self.rels:
            self.assertTrue(problems(rel, "ss -tulpJ"))
            self.assertTrue(problems(rel, "tar --nonexistent -czf a.tgz /etc"))
            self.assertTrue(problems(rel, "find / -sizee +1G"))

    def test_misspelled_subcommand_is_caught(self):
        for rel in self.rels:
            self.assertTrue(problems(rel, "sudo systemctl restrat nginx"))

    def test_unknown_command_is_caught(self):
        for rel in self.rels:
            self.assertTrue(problems(rel, "frobnicate --now"))

    def test_man_page_spelling_wins(self):
        # apt(8) documents --upgradeable only.
        for rel in self.rels:
            self.assertEqual(problems(rel, "apt list --upgradeable"), [])
            self.assertTrue(problems(rel, "apt list --upgradable"))

    def test_release_differences_are_seen(self):
        by_rel = {rel.release: problems(rel, "apt policy nginx") for rel in self.rels}
        self.assertTrue(by_rel["22.04"])          # not in 22.04's apt(8)
        self.assertEqual(by_rel["24.04"], [])


class Splitting(unittest.TestCase):
    def test_pipes_wrappers_and_exec(self):
        invs = cmdline.parse("sudo find /var -name '*.tmp' -exec rm -f {} + | wc -l")
        self.assertEqual([i.command for i in invs], ["sudo", "find", "rm", "wc"])

    def test_placeholders_and_values(self):
        inv = cmdline.parse("sudo lvextend -r -L +20G <VG>/<LV>")[1]
        self.assertEqual(inv.flags, ["-r", "-L"])
        self.assertIn("<VG>/<LV>", inv.args)

    def test_find_numbers_are_values(self):
        self.assertEqual(cmdline.parse("find . -mtime -7")[0].flags, ["-mtime"])

    def test_chattr_modes_are_arguments(self):
        self.assertEqual(cmdline.parse("chattr -i f")[0].flags, [])


class Danger(unittest.TestCase):
    def test_destructive_found(self):
        for line in ["rm -rf /srv/old", "find /var -name x -delete", "dd if=/dev/zero of=/dev/sdb",
                     "sudo mkfs.ext4 /dev/sdb1", "rsync -a --delete a/ b/"]:
            self.assertEqual(danger.rate(line)[0], "destructive", line)

    def test_dry_run_is_not_destructive(self):
        self.assertNotEqual(danger.rate("rsync -an --delete a/ b/")[0], "destructive")

    def test_read_only(self):
        self.assertEqual(danger.rate("ls -la /etc")[0], "read-only")


class KBCheckStopsTheBuild(unittest.TestCase):
    def _run(self, card):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "cmd"))
            os.makedirs(os.path.join(d, "guides"))
            with open(os.path.join(d, "cmd", "t.json"), "w") as f:
                json.dump([card], f)
            out = io.StringIO()
            with redirect_stdout(out):
                code = check.main(["--kb", d, "--data", os.environ.get("LA_DATA", "/opt/la")])
            return code, out.getvalue()

    def card(self, cmd, danger_level="read-only"):
        return {"name": "ss", "summary": "s", "tasks": ["t"],
                "examples": [{"level": "basic", "cmd": cmd, "does": "d", "danger": danger_level}]}

    def test_good_card_passes(self):
        code, out = self._run(self.card("ss -tulpn"))
        self.assertEqual(code, 0, out)

    def test_planted_bad_flag_fails(self):
        code, out = self._run(self.card("ss -tulpJ"))
        self.assertEqual(code, 1)
        self.assertIn("flag -J", out)

    def test_wrong_danger_label_fails(self):
        card = self.card("rm -rf /tmp/x")
        card["name"] = "rm"
        code, out = self._run(card)
        self.assertEqual(code, 1)
        self.assertIn("labelled read-only", out)


if __name__ == "__main__":
    unittest.main()
