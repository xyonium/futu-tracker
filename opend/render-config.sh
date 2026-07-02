#!/bin/bash
# Render FutuOpenD.xml from environment variables.
#
# Inputs (from docker-compose environment):
#   FUTU_LOGIN_ACCOUNT — Futu user ID / phone / email (REQUIRED)
#   FUTU_API_PORT      — API port to expose (default 11111)
#   FUTU_LANG          — chs | en (default chs)
#   FUTU_LOG_LEVEL     — no | debug | info | warning | error | fatal
#
# Password entry for the FIRST-TIME VNC bootstrap happens in the GUI.
# For subsequent headless CLI restarts, the password MD5 must be in the
# config file so that the CLI process can re-authenticate automatically.
# Set FUTU_LOGIN_PWD_MD5 in .env (generate with: echo -n 'mypass' | md5sum).
#
# The device fingerprint (Device.dat) is still required for a fully clean
# login without SMS re-verification.

set -e

: "${FUTU_LOGIN_ACCOUNT:?FUTU_LOGIN_ACCOUNT must be set in .env}"
: "${FUTU_API_PORT:=11111}"
: "${FUTU_LANG:=chs}"
: "${FUTU_LOG_LEVEL:=info}"

PWD_XML=""
if [ -n "${FUTU_LOGIN_PWD_MD5:-}" ]; then
    PWD_XML="    <login_pwd_md5>${FUTU_LOGIN_PWD_MD5}</login_pwd_md5>"
fi

cat <<XML
<?xml version="1.0" encoding="UTF-8"?>
<futu_opend>
    <!-- Listen on 0.0.0.0 so the tracker container can reach us over the docker network -->
    <ip>0.0.0.0</ip>
    <api_port>${FUTU_API_PORT}</api_port>
    <login_account>${FUTU_LOGIN_ACCOUNT}</login_account>
${PWD_XML}
    <lang>${FUTU_LANG}</lang>
    <log_level>${FUTU_LOG_LEVEL}</log_level>
    <!-- Telnet console for interactive commands (e.g. SMS code entry).
         Bound to 0.0.0.0 inside the container; only exposed on the docker
         bridge network, not to the host. -->
    <telnet_ip>0.0.0.0</telnet_ip>
    <telnet_port>22222</telnet_port>
    <push_proto_type>0</push_proto_type>
    <price_reminder_push>1</price_reminder_push>
    <auto_hold_quote_right>1</auto_hold_quote_right>
    <future_trade_api_time_zone>UTC+8</future_trade_api_time_zone>
    <pdt_protection>1</pdt_protection>
    <dtcall_confirmation>1</dtcall_confirmation>
    <!-- RSA encryption required because OpenD listens on 0.0.0.0 (docker bridge
         network). The SDK detects non-localhost and refuses plaintext.
         Private key is generated on first boot if not present. -->
    <rsa_private_key>/data/rsa_private.pem</rsa_private_key>
</futu_opend>
XML
