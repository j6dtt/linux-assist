"""Tests for the lookups, masking and the model client, run inside the built image:

  docker run --rm -v "$PWD/tests:/tests:ro" --entrypoint python la:1.0 -m unittest discover -s /tests -v

The model is a fake server on 127.0.0.1 inside the test, so these run with --network none.
"""
import io
import json
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from http.server import BaseHTTPRequestHandler, HTTPServer

from la import ask, cli, danger, interpret, llm, search, why
from la.verify import check_line
from la.data import Release
from la.kbdata import KB
from la.mask import Masker


def run_cli(*args, stdin=None):
    buf = io.StringIO()
    with redirect_stdout(buf), redirect_stderr(io.StringIO()):
        code = cli.main(list(args))
    return code, buf.getvalue()


class Shared(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.kb = KB()
        cls.rel = Release("24.04")
        cls.rel22 = Release("22.04")


class Lookups(Shared):
    def test_show_card_and_not_found(self):
        code, out = run_cli("--release", "24.04", "show", "ss")
        self.assertEqual(code, 0)
        self.assertIn("KB ✅", out)
        self.assertIn("sudo ss -tulpn", out)
        code, out = run_cli("show", "nmap", "--release", "24.04")
        self.assertEqual(code, 1)
        self.assertIn("Not found", out)

    def test_show_man_only_page(self):
        code, out = run_cli("--release", "22.04", "show", "tree")
        self.assertEqual(code, 0)
        self.assertIn("[man]", out)
        self.assertIn("SYNOPSIS", out)

    def test_unknown_release(self):
        with self.assertRaises(SystemExit) as e:
            run_cli("--release", "20.04", "show", "ls")
        self.assertIn("not in this build", str(e.exception))

    def test_find_ranks_the_right_card_first(self):
        for q, first in [("change file owner", "chown"), ("kill a process", "kill"),
                         ("set a static ip", "netplan-static-ip"), ("restart a service", "systemctl")]:
            hits = search.search(self.kb, self.rel, q)
            self.assertEqual(hits[0]["id"], first, q)

    def test_explain(self):
        code, out = run_cli("--release", "24.04", "explain", "sudo ss -tulpn")
        self.assertEqual(code, 0)
        self.assertIn("Display TCP sockets", out)
        self.assertIn("from the KB", out)
        code, out = run_cli("--release", "24.04", "explain", "systemctl restrat nginx")
        self.assertEqual(code, 1)
        self.assertIn("🔴", out)
        code, out = run_cli("explain", "apt policy nginx", "--release", "22.04")
        self.assertEqual(code, 1)          # apt policy is only in 24.04's apt(8)
        code, out = run_cli("--release", "24.04", "explain", "find /var/log -name *.gz -delete")
        self.assertIn("DESTRUCTIVE", out)
        self.assertIn("Quote the pattern", out)

    def test_why_specific_beats_generic(self):
        hits = why.match(self.kb, "sshd[1]: Permission denied (publickey).")
        self.assertEqual([h[0]["id"] for h in hits], ["ssh-publickey"])
        code, out = run_cli("--release", "24.04", "why", "No space left on device")
        self.assertEqual(code, 0)
        self.assertIn("df -h", out)

    def test_guide_fill_and_reject(self):
        code, out = run_cli("--release", "24.04", "guide", "lvm-extend", "VG=ubuntu-vg")
        self.assertEqual(code, 0)
        self.assertIn("sudo vgextend ubuntu-vg /dev/<NEW_DISK>", out)
        code, out = run_cli("--release", "24.04", "guide", "add-swap-file", "SIZE=4G;rm")
        self.assertEqual(code, 2)
        code, out = run_cli("--release", "24.04", "guide", "lvm-extnd")
        self.assertIn("lvm-extend", out)


DF = """Filesystem      Size  Used Avail Use% Mounted on
tmpfs           1.6G  2.0M  1.6G   1% /run
/dev/sda2        98G   91G  2.1G  98% /
/dev/sdb1       500G  430G   70G  86% /data
/dev/loop0       64M   64M     0 100% /snap/core20/1
"""
FREE = """               total        used        free      shared  buff/cache   available
Mem:            15Gi        14Gi       200Mi       1.0Gi       500Mi       600Mi
Swap:          4.0Gi       3.0Gi       1.0Gi
"""
VMSTAT = """procs -----------memory---------- ---swap-- -----io---- -system-- ------cpu-----
 r  b   swpd   free   buff  cache   si   so    bi    bo   in   cs us sy id wa st
 1  0      0 100000  2000 300000    0    0     5     9   50   80  2  1 97  0  0
 3  2      0 100000  2000 300000   40   80   500  9000  900 1200 10  5 50 35  0
"""


class Interpret(Shared):
    def run_rules(self, kind, text, opts=None):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = interpret.run(self.rel, self.kb, kind, text, opts or {})
        return code, buf.getvalue()

    def test_df(self):
        code, out = self.run_rules(None, DF)
        self.assertEqual(code, 1)
        self.assertIn("⛔ /  98%", out)
        self.assertIn("⚠ /data  86%", out)
        self.assertNotIn("snap", out)            # loop devices and tmpfs are left out
        self.assertIn("la guide disk-full MOUNT=/data", out)

    def test_free_and_vmstat(self):
        code, out = self.run_rules(None, FREE)
        self.assertIn("memory available: 4% of total", out)
        self.assertIn("swapping", out)
        code, out = self.run_rules(None, VMSTAT)
        self.assertIn("swapping: si up to 40", out)
        self.assertIn("wa 35%", out)

    def test_uptime_never_crashes(self):
        code, out = self.run_rules("uptime", " 10:00 up 3 days, load average: 9.50, 7.20, 6.00", {"cpus": 4})
        self.assertEqual(code, 1)
        code, out = self.run_rules("uptime", "load average: 0.12., x")
        self.assertEqual(code, 2)

    def test_wrong_kind(self):
        code, out = self.run_rules("df", "hello")
        self.assertEqual(code, 2)


PODS = """NAMESPACE     NAME                 READY   STATUS             RESTARTS       AGE
app           web-7d9c8f7b6d-ab    1/1     Running            0              3d
app           api-5f6b7c8d9e-cd    0/1     CrashLoopBackOff   12 (2m ago)    1h
kube-system   coredns-1            0/1     Running            1              9d
batch         job-x                0/1     Completed          0              1d
"""
DOCKER_PS = """CONTAINER ID   IMAGE         COMMAND                  CREATED       STATUS                         PORTS                    NAMES
d0e76b96f831   python:3.13   "gunicorn --config…"     4 weeks ago   Up 4 weeks                     0.0.0.0:8043->8043/tcp   hp-viz
abc123456789   nginx         "/docker-entrypoint.…"   2 days ago    Exited (1) 2 days ago                                   web
def456789012   redis         "docker-entrypoint.s…"   1 hour ago    Restarting (1) 5 seconds ago                            cache
0a1b2c3d4e5f   app           "python app.py"          1 day ago     Up 1 day (unhealthy)           80/tcp                   app
5e6f7a8b9c0d   job           "run.sh"                 1 day ago     Exited (0) 1 day ago                                    job
"""


class Containers(Shared):
    """docker (its man pages) and kubectl / docker compose (pages made from --help)."""

    def test_pages(self):
        for rel in (self.rel, self.rel22):
            get = rel.man("kubectl-get")
            self.assertEqual(get.help_cmd, "kubectl get --help")
            self.assertEqual(get.cite, "kubectl get --help")
            self.assertFalse(get.takes_value("-A"))
            self.assertTrue(get.takes_value("-o"))
            self.assertEqual(rel.man("docker-compose-ps").help_cmd, "docker compose ps --help")
            self.assertEqual(rel.man("docker-container-ls").help_cmd, "")      # a real man page
            self.assertEqual(rel.man("kubectl-top-pods").name, "kubectl-top-pod")  # an alias
            self.assertEqual(rel.commands["docker"][0], "docker-ce-cli")
            self.assertTrue(rel.commands["kubectl"][1])

    def test_subcommands_values_and_inside(self):
        cases = [
            ("kubectl -n kube-system get pods -o wide", True, ["get"], []),
            ("kubectl rollout status deployment/web -n app", True, ["rollout", "status"], []),
            ("kubectl top pods -n app", True, ["top", "pods"], []),
            ("kubectl exec -it web-0 -n app -- ls -la", True, ["exec"], ["ls", "-la"]),
            ("docker container ls -a", True, ["container", "ls"], []),
            ("docker run -it --rm -p 8080:80 nginx ls -la", True, ["run"], ["ls", "-la"]),
            ("docker compose -f x.yml logs --tail 100 web", True, ["compose", "logs"], []),
            ("docker compose exec web ls -la", True, ["compose", "exec"], ["ls", "-la"]),
            ("kubectl get pods --wide", False, ["get"], []),
            ("kubectl vsphere login", False, [], []),
            ("docker container frob", False, ["container"], []),
            ("kubectl rollout frob x", False, ["rollout"], []),
        ]
        for line, ok, path, inside in cases:
            r = check_line(self.rel, line)[0]
            self.assertEqual(r.ok, ok, f"{line}: {r.problems}")
            self.assertEqual(r.subpath, path, line)
            self.assertEqual(r.inside, inside, line)
        r = check_line(self.rel, "docker run -p 8080:80 -d nginx")[0]
        self.assertEqual([f.flag for f in r.flags], ["-p", "-d"])     # -p's value is not the image
        # -f before "logs" is compose's --file; after it, logs' --follow
        r = check_line(self.rel, "docker compose -f x.yml logs -f web")[0]
        self.assertEqual([f.page for f in r.flags], ["docker compose --help", "docker compose logs --help"])
        self.assertIn("file", r.flags[0].desc.lower())

    def test_explain(self):
        code, out = run_cli("--release", "24.04", "explain", "docker run -it --rm -p 8080:80 nginx ls -la")
        self.assertEqual(code, 0)
        self.assertIn("8080:80  (value of -p)", out)
        self.assertIn("ls -la  (the command run inside the container", out)
        code, out = run_cli("--release", "24.04", "explain", "kubectl vsphere login --server=x")
        self.assertEqual(code, 1)
        self.assertIn("plugins", out)
        code, out = run_cli("--release", "24.04", "explain", "kubectl -n app delete pod web-0")
        self.assertIn("DESTRUCTIVE", out)
        self.assertIn("kubectl config current-context", out)

    def test_danger(self):
        for line, level in [("docker ps -a", "read-only"), ("docker compose -f x.yml ps", "read-only"),
                            ("docker exec -it web ls", "modify"), ("docker compose down", "modify"),
                            ("docker compose down -v", "destructive"), ("docker image prune -a", "destructive"),
                            ("docker rmi nginx", "destructive"), ("docker volume rm data", "destructive"),
                            ("kubectl -n kube-system get pods", "read-only"),
                            ("kubectl rollout status deploy/x", "read-only"),
                            ("kubectl rollout restart deploy/x", "modify"),
                            ("kubectl delete pod x -n y", "destructive"),
                            ("kubectl drain n1 --ignore-daemonsets --delete-emptydir-data", "destructive")]:
            self.assertEqual(danger.rate(line)[0], level, line)

    def test_find_guide_why(self):
        hits = search.search(self.kb, self.rel, "which container a process belongs to")
        self.assertEqual(hits[0]["id"], "process-owner")
        hits = search.search(self.kb, self.rel, "list pods")
        self.assertEqual(hits[0]["id"], "kubectl")
        code, out = run_cli("--release", "24.04", "guide", "process-owner", "PID=68110")
        self.assertEqual(code, 0)
        self.assertIn("cat /proc/68110/cgroup", out)
        self.assertIn("systemctl status 68110", out)
        hits = why.match(self.kb, "Got permission denied while trying to connect to the Docker daemon socket")
        self.assertEqual([h[0]["id"] for h in hits], ["docker-socket"])
        self.assertEqual(why.match(self.kb, "Back-off restarting failed container")[0][0]["id"], "k8s-crashloop")

    def test_show_subcommand(self):
        code, out = run_cli("--release", "24.04", "show", "kubectl", "rollout", "status")
        self.assertEqual(code, 0)
        self.assertIn("[help]", out)
        code, out = run_cli("--release", "24.04", "show", "kubectl", "frob")
        self.assertEqual(code, 2)

    def test_interpret(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = interpret.run(self.rel, self.kb, None, PODS, {})
        out = buf.getvalue()
        self.assertEqual(code, 1)
        self.assertIn("app/api-5f6b7c8d9e-cd: CrashLoopBackOff, 12 restarts", out)
        self.assertIn("kube-system/coredns-1: running but not ready (0/1)", out)
        self.assertNotIn("job-x", out)
        self.assertIn("la guide pod-not-running NAMESPACE=app POD=api-5f6b7c8d9e-cd", out)
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = interpret.run(self.rel, self.kb, None, DOCKER_PS, {})
        out = buf.getvalue()
        self.assertEqual(code, 1)
        for text in ("web: exited 1", "cache: keeps restarting", "app: unhealthy", "job: exited 0"):
            self.assertIn(text, out)
        self.assertNotIn("hp-viz", out)

    def test_ask_gets_the_subcommand_page(self):
        blocks, ids, hits = ask.retrieve(self.rel, self.kb, "how do I see kubectl logs of a pod that crashed")
        self.assertIn("man:kubectl-logs(1)", ids)


class Masking(unittest.TestCase):
    TEXT = ("ssh deploy@db01.prod.acme.com fails; ping 10.20.30.40, gw 10.20.30.1/24, "
            "mac aa:bb:cc:dd:ee:ff, v6 fe80::1ff:fe23:4567:890a; /home/jsmith/.ssh; "
            "prdapp01 has /etc/nginx/sites-available/app.acme.conf; scp f admin@web01:/tmp; "
            "keep 127.0.0.53, 255.255.255.0, archive.ubuntu.com, backup.tar.gz, nginx.service, 1.2.3")

    def test_round_trip(self):
        m = Masker(words=["acme", "prdapp01"])
        hidden = m.hide(self.TEXT)
        for real in ["deploy", "db01", "acme", "10.20.30.40", "aa:bb:cc", "fe80::", "jsmith",
                     "prdapp01", "web01"]:
            self.assertNotIn(real, hidden)
        for kept in ["127.0.0.53", "255.255.255.0", "archive.ubuntu.com", "backup.tar.gz",
                     "nginx.service", "1.2.3"]:
            self.assertIn(kept, hidden)
        self.assertEqual(m.show(hidden), self.TEXT)

    def test_existing_standin_not_reused(self):
        m = Masker()
        hidden = m.hide("ping 198.18.0.1 then 10.0.0.5")
        self.assertIn("198.18.0.2", hidden)
        self.assertEqual(m.show("try 198.18.0.2 and 198.18.0.20"), "try 10.0.0.5 and 198.18.0.20")

    def test_off(self):
        m = Masker(on=False)
        self.assertEqual(m.hide(self.TEXT), self.TEXT)


# ---------- the model client, against a fake server ----------

class FakeModel(BaseHTTPRequestHandler):
    replies = []          # each: (http code, body dict)
    seen = []

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        FakeModel.seen.append((json.loads(self.rfile.read(n)), self.headers.get("Authorization")))
        code, body = FakeModel.replies.pop(0)
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


def answer(obj, finish="stop"):
    content = obj if isinstance(obj, str) else json.dumps(obj)
    return 200, {"choices": [{"message": {"content": content}, "finish_reason": finish}],
                 "usage": {"completion_tokens": 42}}


class Model(Shared):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.server = HTTPServer(("127.0.0.1", 0), FakeModel)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.env = {"LA_LLM_URL": f"http://127.0.0.1:{cls.server.server_port}/v1",
                   "LA_LLM_MODEL": "nvidia/nemotron-3-super-120b-a12b", "LA_LLM_REASONING": "off",
                   "LLM_API_KEY": "nvapi-secret-test-key-123456", "LA_MASK": "on",
                   "LA_HOSTNAME": "prdweb07"}

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        FakeModel.replies, FakeModel.seen = [], []

    def ask(self, question, env=None):
        buf = io.StringIO()
        with redirect_stdout(buf), redirect_stderr(io.StringIO()):
            code = ask.run(self.rel, self.kb, question, env=env or self.env)
        return code, buf.getvalue()

    def test_body_per_model(self):
        s = llm.Settings({"LA_LLM_MODEL": "gpt-5.4", "LA_LLM_REASONING": "off"})
        b = llm.body(s, [])
        self.assertEqual(b["reasoning_effort"], "none")
        self.assertIn("max_completion_tokens", b)
        s = llm.Settings({"LA_LLM_MODEL": "nvidia/nemotron-3-super-120b-a12b", "LA_LLM_REASONING": "off"})
        b = llm.body(s, [])
        self.assertEqual(b["chat_template_kwargs"], {"enable_thinking": False})
        self.assertIn("max_tokens", b)
        self.assertEqual(b["temperature"], 0)

    def test_parse_json(self):
        self.assertEqual(llm.parse_json('```json\n{"a": 1}\n```'), {"a": 1})
        self.assertEqual(llm.parse_json('Here: {"a": 1} done'), {"a": 1})
        with self.assertRaises(llm.LLMError):
            llm.parse_json("no json")

    def test_good_answer_is_masked_and_checked(self):
        FakeModel.replies = [answer({"answer": "Use ss.", "steps": [
            {"cmd": "sudo ss -tulpn", "why": "ports", "source": "kb:ss"},
            {"cmd": "ping -c 3 198.18.0.1", "why": "reach it", "source": "none"}], "not_covered": ""})]
        code, out = self.ask("is port 443 open on 10.9.8.7 on prdweb07?")
        self.assertEqual(code, 0)
        sent, auth = FakeModel.seen[0]
        text = json.dumps(sent)
        self.assertNotIn("10.9.8.7", text)
        self.assertNotIn("prdweb07", text)
        self.assertEqual(auth, "Bearer nvapi-secret-test-key-123456")
        self.assertIn("ping -c 3 10.9.8.7", out)        # swapped back
        self.assertIn("✅ KB example (ss)", out)
        self.assertIn("🟡 checked in ping(8)", out)

    def test_bad_flag_retried_then_fixed(self):
        FakeModel.replies = [
            answer({"answer": "x", "steps": [{"cmd": "ss -tulpnQ", "why": "", "source": "kb:ss"}]}),
            answer({"answer": "x", "steps": [{"cmd": "ss -tulpn", "why": "", "source": "kb:ss"}]})]
        code, out = self.ask("which ports listen")
        self.assertEqual(code, 0)
        self.assertEqual(len(FakeModel.seen), 2)
        self.assertIn("-Q", FakeModel.seen[1][0]["messages"][-1]["content"])
        self.assertIn("asked once more", out)

    def test_bad_flag_twice_is_red(self):
        bad = answer({"answer": "x", "steps": [{"cmd": "ss --made-up", "why": "", "source": "kb:ss"}]})
        FakeModel.replies = [bad, bad]
        code, out = self.ask("which ports listen")
        self.assertEqual(code, 1)
        self.assertIn("🔴 do not run", out)

    def test_thinking_used_all_tokens(self):
        FakeModel.replies = [(200, {"choices": [{"message": {"content": None, "reasoning_content": "hmm"},
                                                 "finish_reason": "length"}]})]
        code, out = self.ask("free up disk space")
        self.assertEqual(code, 3)
        self.assertIn("all 2000 reply tokens thinking", out)
        self.assertIn("la guide disk-full", out)         # fell back to the KB search

    def test_refused_key_never_shown(self):
        FakeModel.replies = [(401, {"error": {"message": "bad key nvapi-secret-test-key-123456"}})]
        code, out = self.ask("free up disk space")
        self.assertEqual(code, 3)
        self.assertIn("HTTP 401", out)
        self.assertNotIn("secret-test-key", out)

    def test_unreachable_and_placeholder(self):
        env = dict(self.env, LA_LLM_URL="http://127.0.0.1:9/v1")
        code, out = self.ask("free up disk space", env)
        self.assertEqual(code, 3)
        self.assertIn("Cannot reach the model server", out)
        env = dict(self.env, LA_LLM_URL="http://<vllm-host>:8000/v1")
        code, out = self.ask("free up disk space", env)
        self.assertIn("placeholder", out)


if __name__ == "__main__":
    unittest.main()
