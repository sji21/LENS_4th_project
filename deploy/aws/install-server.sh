#!/usr/bin/env bash
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
APP=/opt/lens/app
VENV=/opt/lens/venv311
# Initial provisioning helper, NOT the production update procedure.
# Prerequisites and the MySQL release activation step are documented in README.md.
REVISION=40ac9d706c4959ab14045349d47ecab55ad228a7

if [ ! -d "$APP/.git" ]; then
    sudo -u lens git -c filter.lfs.smudge= -c filter.lfs.required=false clone --no-checkout --depth 1 https://github.com/sji21/LENS_4th_project.git "$APP"
    sudo -u lens git -C "$APP" fetch --depth 1 origin "$REVISION"
    sudo -u lens git -c filter.lfs.smudge= -c filter.lfs.required=false -C "$APP" checkout --detach "$REVISION"
fi
test "$(git -c safe.directory="$APP" -C "$APP" rev-parse HEAD)" = "$REVISION"
sudo -u lens git -C "$APP" lfs pull --include='data/case_corpus/**'
install -d -o lens -g lens "$APP/tmp" "$APP/data/database" "$APP/deploy/aws"
install -o lens -g lens -m 644 /home/ubuntu/lens-deploy/production.py "$APP/config/production.py"
install -o lens -g lens -m 644 /home/ubuntu/lens-deploy/gunicorn.conf.py "$APP/deploy/aws/gunicorn.conf.py"
install -o lens -g lens -m 644 /home/ubuntu/lens-deploy/constraints-python311.txt "$APP/deploy/aws/constraints-python311.txt"
# Keep Ubuntu's system Python unchanged; install an isolated Python 3.11 runtime.
python3 -m venv /opt/lens/bootstrap
/opt/lens/bootstrap/bin/pip install 'uv==0.12.17'
install -d -o lens -g lens /opt/lens/python /opt/lens/cache/uv
cd "$APP"
if [ ! -x "$VENV/bin/python" ]; then
    sudo -u lens env UV_PYTHON_INSTALL_DIR=/opt/lens/python UV_CACHE_DIR=/opt/lens/cache/uv \
        /opt/lens/bootstrap/bin/uv --no-config venv --python 3.11 --seed "$VENV"
fi
"$VENV/bin/python" -c 'import sys; assert sys.version_info[:2] == (3, 11)'
sudo -u lens "$VENV/bin/pip" install --upgrade pip
# The web host has no GPU. Avoid downloading unused CUDA packages.
sudo -u lens "$VENV/bin/pip" install -c "$APP/deploy/aws/constraints-python311.txt" torch --index-url https://download.pytorch.org/whl/cpu
sudo -u lens "$VENV/bin/pip" install -c "$APP/deploy/aws/constraints-python311.txt" 'gunicorn>=23,<24'
sudo -u lens env HF_HOME=/opt/lens/cache/huggingface OMP_NUM_THREADS=2 TOKENIZERS_PARALLELISM=false PIP_CONSTRAINT="$APP/deploy/aws/constraints-python311.txt" \
    "$VENV/bin/python" "$APP/setup_data.py" --venv-dir "$VENV"

if [ ! -f /etc/lens/lens.env ]; then
    python3 - <<'PY'
import base64, os, secrets
from pathlib import Path
values = {
    'DJANGO_SECRET_KEY': secrets.token_urlsafe(64),
    'DJANGO_DEBUG': 'false',
    'DJANGO_ALLOWED_HOSTS': 'd3kro62a2qvg04.cloudfront.net',
    'LENS_FILE_ENCRYPTION_KEY': base64.urlsafe_b64encode(os.urandom(32)).decode(),
    'LENS_MYSQL_RELEASE': '',
    'LENS_MYSQL_MODEL_DIR': '',
    'JEONSEON_LLM_BASE_URL': 'http://127.0.0.1:11434/v1',
    'JEONSEON_LLM_MODEL': 'qwen3.8:27b',
    'JEONSEON_LLM_TIMEOUT': '90',
    'LANGSMITH_TRACING': 'false',
    'OMP_NUM_THREADS': '2',
    'TOKENIZERS_PARALLELISM': 'false',
}
path = Path('/etc/lens/lens.env')
path.write_text(''.join(f'{k}={v}\n' for k, v in values.items()))
path.chmod(0o600)
PY
    chown lens:lens /etc/lens/lens.env
fi
ln -sfn /etc/lens/lens.env "$APP/.env"
cd "$APP"
sudo -u lens env DJANGO_SETTINGS_MODULE=config.production "$VENV/bin/python" manage.py migrate --noinput
sudo -u lens env DJANGO_SETTINGS_MODULE=config.production "$VENV/bin/python" manage.py collectstatic --noinput
sudo -u lens env DJANGO_SETTINGS_MODULE=config.production "$VENV/bin/python" manage.py check --deploy
chmod -R a+rX "$APP/data/staticfiles"
chmod o+x /opt/lens

python3 - <<'PY'
from pathlib import Path
token = Path('/home/ubuntu/lens-deploy/origin-token').read_text().strip()
assert len(token) == 64 and all(c in '0123456789abcdef' for c in token)
config = '''server {
    listen 80 default_server;
    server_name _;
    client_max_body_size 21m;
    server_tokens off;
    if ($http_x_lens_origin_token != "TOKEN") { return 403; }
    location /static/ {
        alias /opt/lens/app/data/staticfiles/;
        access_log off;
    }
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Lens-Origin-Token "";
        proxy_read_timeout 240s;
        proxy_send_timeout 240s;
        proxy_request_buffering on;
    }
}
'''.replace('TOKEN', token)
Path('/etc/nginx/sites-available/lens').write_text(config)
Path('/home/ubuntu/lens-deploy/origin-token').unlink()
PY
if [ -L /etc/nginx/sites-enabled/default ]; then
    unlink /etc/nginx/sites-enabled/default
fi
ln -sfn /etc/nginx/sites-available/lens /etc/nginx/sites-enabled/lens
nginx -t
install -m 644 /home/ubuntu/lens-deploy/lens.service /etc/systemd/system/lens.service
systemctl daemon-reload
systemctl enable --now lens
systemctl enable --now nginx
echo 'LENS_WEB_INSTALL_COMPLETE: LLM endpoint still requires configuration and verification.'
