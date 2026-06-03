#!/bin/bash
source .venv/bin/activate
export $(cat .env | grep -v '#' | xargs)
# For local proxy with self-signed certificates, uncomment the next line:
# exec sraf --no-verify-ssl "$@"
exec sraf "$@"
