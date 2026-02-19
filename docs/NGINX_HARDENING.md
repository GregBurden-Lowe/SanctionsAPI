# Nginx Hardening (Droplet)

Use this when your app is internet-facing behind Nginx.

## 1) Create site config

Create `/etc/nginx/sites-available/sanctions-api.conf`:

```nginx
# Global edge rate limits (define once in http{} context; sites-available is loaded there on Ubuntu)
limit_req_zone $binary_remote_addr zone=api_per_ip:10m rate=20r/m;
limit_conn_zone $binary_remote_addr zone=conn_per_ip:10m;

upstream sanctions_api_upstream {
    server 127.0.0.1:8000;
    keepalive 16;
}

server {
    listen 80;
    server_name your-domain.com;

    # Block hidden files and common scanner targets early.
    location ~ /\.(?!well-known).* { return 403; }
    location ~* ^/(?:wp-admin|wp-login|phpmyadmin|manager/html) { return 444; }

    # Block common upload/probe paths (bots hitting generic upload endpoints).
    location ~* ^/(?:api/)?(?:v[0-9]+/)?(?:upwload|upload|uploads|uploadfile|fileupload|file-upload|files/upload|media/upload|images/upload|blob/upload|multipart|bulk-upload|batch/upload|drive/upload|s3/upload|storage/upload|admin/upload|admin/files|admin/media)$ {
        return 444;
    }

    location / {
        # Edge anti-abuse controls
        limit_req zone=api_per_ip burst=40 nodelay;
        limit_conn conn_per_ip 40;

        proxy_pass http://sanctions_api_upstream;
        proxy_http_version 1.1;
        proxy_set_header Connection "";

        # Preserve client identity for app logic/rate-limit keying.
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # Tight but safe defaults
        proxy_connect_timeout 5s;
        proxy_send_timeout 60s;
        proxy_read_timeout 60s;
        send_timeout 60s;
    }
}
```

## 2) Enable and validate

```bash
sudo ln -sf /etc/nginx/sites-available/sanctions-api.conf /etc/nginx/sites-enabled/sanctions-api.conf
sudo nginx -t
sudo systemctl reload nginx
```

## 3) App env alignment

In your app `.env`, ensure:

```env
TRUSTED_PROXY_IPS=127.0.0.1,::1
```

This allows the app to trust `X-Forwarded-For` only from local Nginx.

## 4) Verify

```bash
curl -i http://127.0.0.1:8000/health
curl -i https://your-domain.com/health
curl -i -X POST https://your-domain.com/upload
```

Expected:
- `/health` returns `200`.
- probe-style upload path returns `444`/connection close from Nginx.
