# Runbook: Renew the Ingest TLS Certificate

Synthetic document, Larkspur Systems wiki (fictional).

The ingest gateway `tlm-ingest` serves HTTPS on port 8443. Its certificate is stored at `/etc/larkspur/tls/ingest.pem`. Each certificate is valid for 90 days.

## When to renew

Renew the ingest certificate when 30 days remain. Check the remaining time with `lsctl cert show --service ingest`. The command prints the expiry date and the number of days left.

Normally the renewal timer does this work. Use this manual runbook only when the timer failed or an alert named `cert_expiry_warn` is open.

## Steps

1. Log in to the gateway host `gw-01` or `gw-02`.
2. Run `lsctl cert renew --service ingest`. The command requests a new certificate from the internal certificate authority.
3. The command writes the new file next to the old one as `ingest.pem.new`.
4. Check the new file with `lsctl cert verify /etc/larkspur/tls/ingest.pem.new`. The output must say `chain ok`.
5. Move the new file over the old one: `mv ingest.pem.new ingest.pem`.
6. Reload the gateway with `kill -HUP $(pidof tlm-ingest)` after the new file is in place.
7. Run `lsctl cert show --service ingest` again and confirm that 90 days remain.

Repeat the steps on the second gateway host. Do both hosts in one session. Do not leave one host on an older certificate.

## Do not restart

A reload with SIGHUP keeps open connections. A full restart drops every upload in flight. Nodes retry dropped uploads, but a full restart during a launch window adds load for no gain.

## If renewal fails

If the certificate authority returns an error, copy the full error text into the ticket. Do not retry more than twice. Page the platform lead. A certificate that has already expired is a SEV1 case because nodes reject the gateway and stop uploading.

## Related pages

The incident report for the December 2025 expiry explains why the timer was added. The decision record on certificate automation sets the timer interval.
