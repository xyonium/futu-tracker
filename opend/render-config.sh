#!/bin/bash
# Render FutuOpenD.xml from environment variables.
#
# Inputs (from docker-compose environment):
#   FUTU_LOGIN_ACCOUNT — Futu user ID / phone / email (REQUIRED)
#   FUTU_API_PORT      — API port to expose (default 11111)
#   FUTU_LANG          — chs | en (default chs)
#   FUTU_LOG_LEVEL     — no | debug | info | warning | error | fatal
#
# We deliberately do NOT bake login_pwd_md5 into the config. Password entry
# happens inside the VNC bootstrap GUI, and the OpenD saves the login token
# to the userdata directory (which is what the fingerprint check protects).

set -e

: "${FUTU_LOGIN_ACCOUNT:?FUTU_LOGIN_ACCOUNT must be set in .env}"
: "${FUTU_API_PORT:=11111}"
: "${FUTU_LANG:=chs}"
: "${FUTU_LOG_LEVEL:=info}"

cat <<XML
<?xml version="1.0" encoding="UTF-8"?>
<futu_opend>
    <!-- Listen on 0.0.0.0 so the tracker container can reach us over the docker network -->
    <ip>0.0.0.0</ip>
    <api_port>${FUTU_API_PORT}</api_port>
    <login_account>${FUTU_LOGIN_ACCOUNT}</login_account>
    <lang>${FUTU_LANG}</lang>
    <log_level>${FUTU_LOG_LEVEL}</log_level>
    <push_proto_type>0</push_proto_type>
    <price_reminder_push>1</price_reminder_push>
    <auto_hold_quote_right>1</auto_hold_quote_right>
    <future_trade_api_time_zone>UTC+8</future_trade_api_time_zone>
    <pdt_protection>1</pdt_protection>
    <dtcall_confirmation>1</dtcall_confirmation>
</futu_opend>
XML
