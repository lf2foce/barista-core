#!/bin/sh
# Chạy MỘT lần khi volume Postgres còn trống: hai role như backend/scripts/dev-db.sh — barista_owner (migration, quét xuyên công ty,
# BYPASSRLS) và barista_app (mọi request, KHÔNG BYPASSRLS — RLS là lưới cuối).
set -e
psql -v ON_ERROR_STOP=1 -U postgres -d postgres <<SQL
CREATE ROLE barista_owner LOGIN PASSWORD '${OWNER_PASSWORD}' NOSUPERUSER BYPASSRLS;
CREATE ROLE barista_app LOGIN PASSWORD '${APP_PASSWORD}' NOSUPERUSER NOBYPASSRLS;
CREATE DATABASE barista OWNER barista_owner;
GRANT CONNECT ON DATABASE barista TO barista_app;
SQL
