#!/usr/bin/env python3
"""Host-side blue/green rollout. Never migrates, force-kills or runs compose down."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import urllib.request
import uuid

COMPOSE = Path(__file__).resolve().parents[1] / 'deploy/compose.rollout.yml'


def run(args, env=None, timeout=1200):
    # Do not print environment values or Docker inspect output containing secrets.
    result = subprocess.run(args, env=env, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(f'{args[0]} {args[1]} failed (exit {result.returncode}); inspect service logs on the host')
    return result.stdout.strip()


def atomic_write(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, 'wb' if isinstance(text, bytes) else 'w') as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def get_ready(url):
    request = urllib.request.Request(url, headers={'Connection':'close', 'Cache-Control':'no-cache'})
    with urllib.request.urlopen(request, timeout=8) as response:
        return json.load(response)


def require_compatible(active, candidate):
    for result in (active, candidate):
        if (result.get('status') != 'ready' or result.get('schema_mode') != 'verify'
                or not all(result.get(key) for key in ('release','protocol','schema_contract'))):
            raise RuntimeError('A verified rolling baseline is required; use the coordinated first-upgrade procedure')
    if any(active.get(key) != candidate.get(key) for key in ('protocol', 'schema_contract')):
        raise RuntimeError('Incompatible release: coordinated schema/worker upgrade required')


def process_identity(pid):
    try:
        return Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[19]
    except FileNotFoundError:
        return None


def nginx_workers(pid_file):
    master = int(Path(pid_file).read_text().strip())
    children = Path(f'/proc/{master}/task/{master}/children').read_text().split()
    workers = {}
    for pid in children:
        try:
            command = Path(f'/proc/{pid}/cmdline').read_text()
            if 'nginx: worker process' in command:
                identity = process_identity(pid)
                if identity is not None:
                    workers[pid] = identity
        except FileNotFoundError:
            pass
    if not workers:
        raise RuntimeError('Cannot identify Nginx workers; refusing an unverified drain')
    return workers


class Deployment:
    def __init__(self, config):
        self.c = config
        self.directory = Path(config['state_dir'])
        self.directory.mkdir(parents=True, exist_ok=True)
        self.state_path = self.directory / 'state.json'
        self.journal = self.directory / 'pending.json'
        self.upstream = Path(config['upstream_file'])
        if int(config['blue_port']) == int(config['green_port']):
            raise ValueError('Blue and green ports must differ')
        for slot in ('blue', 'green'):
            if not 1024 <= int(config[f'{slot}_port']) <= 65535:
                raise ValueError('Invalid slot port')
        for key in ('env_file', 'assets_dir', 'state_dir', 'upstream_file', 'nginx_pid_file'):
            if not Path(config[key]).is_absolute():
                raise ValueError(f'{key} must be absolute')
        self.env = {**os.environ, 'ROLLOUT_ENV_FILE':config['env_file'],
                    'ROLLOUT_ASSETS':config['assets_dir'], 'ROLLOUT_NETWORK':config['network'],
                    'ROLLOUT_DATA_VOLUME':config['data_volume'],
                    'ROLLOUT_BLUE_PORT':str(config['blue_port']), 'ROLLOUT_GREEN_PORT':str(config['green_port'])}

    def compose(self, *args, image=None):
        image = image or 'unused:inspection-only'
        return run(['docker','compose','-p',self.c['project'],'-f',str(COMPOSE), *args],
                   env={**self.env, 'ROLLOUT_IMAGE':image, 'ROLLOUT_RELEASE':image})

    def container(self, service):
        return self.compose('ps','-a','-q',service)

    def state(self):
        return json.loads(self.state_path.read_text())

    def save(self, state):
        atomic_write(self.state_path, json.dumps(state, indent=2))

    def direct(self, slot):
        return f"http://127.0.0.1:{self.c[slot + '_port']}/ready"

    def upstream_text(self, slot):
        return f"server 127.0.0.1:{self.c[slot + '_port']};\n"

    def wait_ready(self, slot, image, edge=False):
        deadline = time.monotonic() + self.c.get('ready_timeout',180)
        success = 0
        while time.monotonic() < deadline:
            try:
                report = get_ready(self.c['edge_ready_url'] if edge else self.direct(slot))
                if report.get('release') == image and report.get('status') == 'ready':
                    success += 1
                    if success == 3:
                        return report
                else:
                    success = 0
            except Exception:
                success = 0
            time.sleep(1)
        raise RuntimeError('Readiness or expected release check timed out')

    def wait_worker(self, slot):
        container = self.container('worker-' + slot)
        deadline = time.monotonic() + self.c.get('ready_timeout',180)
        while time.monotonic() < deadline:
            if run(['docker','inspect','--format','{{.State.Health.Status}}',container]) == 'healthy':
                return
            time.sleep(1)
        raise RuntimeError('Candidate worker did not become healthy')

    def copy_assets(self, image):
        name = 'sanctions-assets-' + uuid.uuid4().hex
        run(['docker','create','--name',name,image])
        try:
            with tempfile.TemporaryDirectory() as temporary:
                run(['docker','cp',name + ':/app/frontend/dist/assets/.',temporary])
                target = Path(self.c['assets_dir'])
                target.mkdir(parents=True, exist_ok=True)
                files = list(Path(temporary).rglob('*'))
                for source in files:
                    if source.is_symlink():
                        raise RuntimeError('Unexpected asset symlink')
                    if source.is_file():
                        dest = target / source.relative_to(temporary)
                        if dest.exists() and dest.read_bytes() != source.read_bytes():
                            raise RuntimeError('Asset name collision; refusing to overwrite an older release asset')
                for source in files:
                    if source.is_file():
                        dest = target / source.relative_to(temporary)
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        if not dest.exists():
                            atomic_write(dest, source.read_bytes())
        finally:
            run(['docker','rm',name])

    def prepare(self, slot, image):
        for service in ('api-' + slot, 'worker-' + slot):
            container = self.container(service)
            if container and run(['docker','inspect','--format','{{.State.Running}}',container]) == 'true':
                raise RuntimeError(f'{service} is still running; retire it before reusing this slot')
        self.copy_assets(image)
        self.compose('up','-d','--no-deps','--force-recreate','api-' + slot,'worker-' + slot, image=image)
        report = self.wait_ready(slot, image)
        self.wait_worker(slot)
        return report

    def reload(self, text):
        atomic_write(self.upstream, text)
        run(['nginx','-t'])
        run(['systemctl','reload','nginx'])

    def retire(self):
        state = self.state()
        pending = state.get('retiring')
        if not pending:
            return
        self.wait_ready(state['slot'], state['image'], edge=True)
        deadline = time.monotonic() + self.c.get('drain_timeout',720)
        while any(process_identity(pid) == identity for pid,identity in pending['workers'].items()):
            if time.monotonic() > deadline:
                raise RuntimeError('Old Nginx requests are still draining; both slots retained. Run retire later')
            time.sleep(1)
        # No force kill, even if a worker is slow. A timed-out host command leaves
        # the daemon waiting for graceful exit and retains retirement state.
        for service in ('api-', 'worker-'):
            container = self.container(service + pending['slot'])
            if container:
                run(['docker','stop','--timeout','-1',container])
        state.pop('retiring')
        self.save(state)

    def recover(self):
        journal = json.loads(self.journal.read_text())
        old = journal['old']
        self.wait_ready(old['slot'], old['image'])
        draining = nginx_workers(self.c['nginx_pid_file'])
        self.reload(journal['upstream'])
        self.wait_ready(old['slot'], old['image'], edge=True)
        old['retiring'] = {'slot':journal['target'], 'workers':draining}
        self.save(old)
        self.journal.unlink()
        print('Previous upstream restored. Both slots retained until retire completes.', flush=True)

    def deploy(self, image):
        if self.journal.exists():
            raise RuntimeError('Interrupted rollout detected. Run recover before another rollout')
        self.retire()
        old = self.state()
        active = self.wait_ready(old['slot'], old['image'], edge=True)
        require_compatible(active, active)
        if self.upstream.read_text() != self.upstream_text(old['slot']):
            raise RuntimeError('Upstream file drift detected')
        # Inspect candidate protocol WITHOUT starting its API/worker against the DB.
        candidate = json.loads(run(['docker','run','--rm','--network','none','--entrypoint','python',image,
            '-c', "import json; from deployment_runtime import PROTOCOL,schema_contract; print(json.dumps(dict(status='ready',schema_mode='verify',release='candidate',protocol=PROTOCOL,schema_contract=schema_contract())))"]))
        require_compatible(active, candidate)
        target = 'green' if old['slot'] == 'blue' else 'blue'
        print(f'Preparing {target}; {old["slot"]} continues serving.', flush=True)
        atomic_write(self.journal, json.dumps({'old':old, 'target':target, 'upstream':self.upstream.read_text()}))
        try:
            ready = self.prepare(target, image)
            require_compatible(active, ready)
            workers = nginx_workers(self.c['nginx_pid_file'])
            self.reload(self.upstream_text(target))
            self.wait_ready(target, image, edge=True)
            self.save({'slot':target, 'image':image, 'previous_image':old['image'],
                       'retiring':{'slot':old['slot'], 'workers':workers}})
            self.journal.unlink()
        except BaseException:
            # If rollback itself fails, keep the journal and both containers.
            self.recover()
            raise
        print(f'Traffic verified on {target}. Waiting for old connections and jobs to drain.', flush=True)
        self.retire()
        print('Rollout complete.', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['bootstrap','adopt','deploy','rollback','retire','recover','status'])
    parser.add_argument('--config', required=True)
    parser.add_argument('--image', help='Already built/pulled release image; resolved to immutable ID')
    args = parser.parse_args()
    deployment = Deployment(json.loads(Path(args.config).read_text()))
    with (deployment.directory / 'deploy.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.action == 'status':
            print(deployment.state_path.read_text() if deployment.state_path.exists() else 'No adopted baseline')
            print('Pending recovery:', deployment.journal.exists())
            return
        if deployment.journal.exists() and args.action != 'recover':
            raise RuntimeError('Run recover for the interrupted deployment first')
        if args.action in ('bootstrap','deploy'):
            if not args.image:
                parser.error('--image is required')
            image = run(['docker','image','inspect','--format','{{.Id}}',args.image])
            if args.action == 'bootstrap':
                if deployment.state_path.exists():
                    raise RuntimeError('Baseline already adopted')
                ready = deployment.prepare('blue', image)
                require_compatible(ready, ready)
                print('Blue is ready. Configure the Nginx include, verify traffic, then run adopt.')
            else:
                deployment.deploy(image)
        elif args.action == 'adopt':
            if deployment.state_path.exists():
                raise RuntimeError('Baseline already adopted')
            ready = get_ready(deployment.direct('blue'))
            require_compatible(ready, ready)
            if deployment.upstream.read_text() != deployment.upstream_text('blue'):
                raise RuntimeError('Nginx include must point at blue before adoption')
            deployment.wait_ready('blue', ready['release'], edge=True)
            deployment.wait_worker('blue')
            deployment.save({'slot':'blue', 'image':ready['release']})
        elif args.action == 'rollback':
            image = deployment.state().get('previous_image')
            if not image:
                raise RuntimeError('No previous image recorded')
            deployment.deploy(image)
        else:
            getattr(deployment, args.action)()


if __name__ == '__main__':
    import signal
    def interrupted(*_):
        raise KeyboardInterrupt('Deployment interrupted')
    signal.signal(signal.SIGTERM, interrupted)
    main()
