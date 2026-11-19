#!/usr/bin/env bash
# Optional: repair the school's incomplete public TLS chain without disabling TLS.
# Output is the CA-bundle path. Uses certifi's independently distributed root store.
set -euo pipefail
cert_dir=${1:?certificate output directory required}
mkdir -p "$cert_dir"
python - "$cert_dir" <<'PY'
import sys, urllib.request
from pathlib import Path
out = Path(sys.argv[1])
# This is a public issuing certificate, not a credential. Its signature must be
# verified against certifi's roots below before it can enter the bundle.
with urllib.request.urlopen('http://cert.ssl.com/SSL.com-TLS-I-RSA-R1.cer', timeout=30) as response:
    (out / 'issuing.der').write_bytes(response.read())
PY
openssl x509 -inform DER -in "$cert_dir/issuing.der" -out "$cert_dir/issuing.pem"
ca_roots=$(python -c 'import certifi; print(certifi.where())')
openssl verify -CAfile "$ca_roots" "$cert_dir/issuing.pem" >&2
cat "$ca_roots" "$cert_dir/issuing.pem" > "$cert_dir/verified-ca-bundle.pem"
printf '%s\n' "$cert_dir/verified-ca-bundle.pem"
