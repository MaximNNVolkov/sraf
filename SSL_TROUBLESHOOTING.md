# SSL Certificate Issue - Troubleshooting

## Problem
When running `sraf` with real GigaChat credentials, you get:
```
FileNotFoundError: [Errno 2] No such file or directory
```

This is caused by SSL certificate verification issues, likely due to:
1. Local proxy with self-signed certificate
2. Corporate proxy
3. Missing Russian Trusted Root CA certificate

## Solutions

### Solution 1: Use Russian Trusted Root CA (Recommended for production)

1. Download the certificate from Gosuslugi:
```bash
wget -O Russian_Trusted_Root_CA.crt https://www.gosuslugi.ru/crt
```

2. Set environment variable:
```bash
export GIGACHAT_CA_BUNDLE_FILE=/path/to/Russian_Trusted_Root_CA.crt
```

3. Run normally:
```bash
sraf run "your task"
```

### Solution 2: Disable SSL verification (For development/local proxy only)

Use the `--no-verify-ssl` flag:
```bash
sraf run "your task" --no-verify-ssl
```

Or enable it in `run_sraf.sh`:
```bash
#!/bin/bash
source .venv/bin/activate
export $(cat .env | grep -v '#' | xargs)
exec sraf --no-verify-ssl "$@"
```

Then run:
```bash
./run_sraf.sh run "your task"
./run_sraf.sh chat
```

### Solution 3: Use HTTPS proxy with certificate

If using local HTTPS proxy, configure it properly:
```bash
export HTTPS_PROXY=https://proxy.example.com:8080
sraf run "your task"
```

## Current Status

- ✅ Credentials are correctly configured (base64 encoded client_id:client_secret)
- ✅ CLI parsing and flags work correctly
- ✅ Demo mode works without credentials
- ⚠️ SSL certificate verification needs to be configured

## Testing

### Test with demo mode (no credentials needed):
```bash
sraf run "test" --demo
sraf chat --demo
```

### Test with real API (needs proper SSL setup):
```bash
# Option 1: With SSL verification disabled (dev only)
sraf run "test" --no-verify-ssl

# Option 2: With proper certificate
export GIGACHAT_CA_BUNDLE_FILE=/path/to/cert.crt
sraf run "test"
```
