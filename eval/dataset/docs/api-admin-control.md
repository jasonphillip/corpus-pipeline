# API Reference: Admin Control

Synthetic document, Larkspur Systems wiki (fictional).

The admin API controls nodes and services. The control tool `lsctl` is a client for it. The API listens on port 9102 and serves HTTPS.

## Authentication

Send a bearer token in the `Authorization` header. Tokens carry a role. The roles are:

- `viewer`: read only.
- `ops`: read and control nodes.
- `admin`: all of the above plus token and config management.

Tokens for the `ops` role expire after 8 hours. Tokens for `viewer` expire after 7 days. Tokens for `admin` expire after 1 hour and need a second factor. Get a token with `lsctl login`, which stores it in `~/.config/larkspur/token`.

## Node endpoints

- `GET /v1/nodes`: list all nodes with their state, firmware version and last seen time.
- `GET /v1/nodes/{id}`: details of one node.
- `POST /v1/nodes/{id}/restart`: restart the receiver service. This is what `lsctl restart receiver` calls.
- `POST /v1/nodes/{id}/maintenance`: set maintenance mode. The body is `{"on": true}`.
- `POST /v1/nodes/{id}/power-cycle`: cut and restore power. Requires the role `ops`.

## Fleet endpoints

- `POST /v1/fleet/freq`: load a frequency list. The body is a CSV file.
- `GET /v1/fleet/check`: run the launch readiness check.
- `POST /v1/fw/stage` and `POST /v1/fw/apply`: stage and apply firmware.

## Enrollment

`POST /v1/enroll` takes a one time token and a certificate signing request. It returns a signed node certificate. This endpoint needs no bearer token. The enrollment token is the credential.

## Errors

Errors use a JSON body with the keys `code` and `message`. The code `E_STATE` means the node is not in a state that allows the action, for example a restart during a firmware apply. The code `E_RATE` means the caller sent more than 30 control requests per minute.

## Audit

Every control request is written to the audit log with the caller, the role, the node and the time. Audit entries are kept for 400 days.
