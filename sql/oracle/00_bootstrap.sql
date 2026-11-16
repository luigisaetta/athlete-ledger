-- Run once as ADMIN from SQLcl, connected to the target ADB through its wallet:
--   sql /nolog
--   CONNECT ADMIN@<tns_alias>
--   @sql/oracle/00_bootstrap.sql
-- Enter passwords at hidden prompts. Do not spool or record the session.
-- Supported passwords: 12-128 ASCII letters/digits or _ # $ ! . -;
-- also satisfy the target ADB password policy. No passwords are reset on rerun.
-- The owner receives a 1G DATA quota; adjust it here before running if needed.
-- This is the only hand-written Oracle schema setup; schema.sql is generated.

SET ECHO OFF
SET VERIFY OFF
SET FEEDBACK OFF
SPOOL OFF
WHENEVER SQLERROR EXIT FAILURE ROLLBACK

ACCEPT owner_password CHAR PROMPT 'ATHLETE_LEDGER password: ' HIDE
ACCEPT reader_password CHAR PROMPT 'ATHLETE_LEDGER_RO password: ' HIDE

DECLARE
    owner_secret VARCHAR2(128) := q'[&&owner_password]';
    reader_secret VARCHAR2(128) := q'[&&reader_password]';
    existing_count PLS_INTEGER;
BEGIN
    IF NOT REGEXP_LIKE(owner_secret, '^[A-Za-z0-9_#$!.-]{12,128}$', 'c')
       OR NOT REGEXP_LIKE(reader_secret, '^[A-Za-z0-9_#$!.-]{12,128}$', 'c') THEN
        RAISE_APPLICATION_ERROR(-20001, 'Passwords do not meet the supported format.');
    END IF;

    SELECT COUNT(*) INTO existing_count FROM dba_roles WHERE role = 'ATHLETE_LEDGER_READER';
    IF existing_count = 0 THEN
        EXECUTE IMMEDIATE 'CREATE ROLE ATHLETE_LEDGER_READER';
    END IF;

    SELECT COUNT(*) INTO existing_count FROM dba_users WHERE username = 'ATHLETE_LEDGER';
    IF existing_count = 0 THEN
        EXECUTE IMMEDIATE 'CREATE USER ATHLETE_LEDGER IDENTIFIED BY '
            || DBMS_ASSERT.ENQUOTE_NAME(owner_secret, FALSE)
            || ' DEFAULT TABLESPACE DATA QUOTA 1G ON DATA';
    END IF;
    EXECUTE IMMEDIATE 'ALTER USER ATHLETE_LEDGER QUOTA 1G ON DATA';
    EXECUTE IMMEDIATE 'GRANT CREATE SESSION, CREATE TABLE, CREATE SEQUENCE, CREATE VIEW '
        || 'TO ATHLETE_LEDGER';

    SELECT COUNT(*) INTO existing_count FROM dba_users WHERE username = 'ATHLETE_LEDGER_RO';
    IF existing_count = 0 THEN
        EXECUTE IMMEDIATE 'CREATE USER ATHLETE_LEDGER_RO IDENTIFIED BY '
            || DBMS_ASSERT.ENQUOTE_NAME(reader_secret, FALSE) || ' DEFAULT TABLESPACE DATA';
    END IF;
    EXECUTE IMMEDIATE 'GRANT CREATE SESSION TO ATHLETE_LEDGER_RO';
    EXECUTE IMMEDIATE 'GRANT ATHLETE_LEDGER_READER TO ATHLETE_LEDGER_RO';
EXCEPTION
    WHEN OTHERS THEN
        -- Do not echo driver diagnostics that could include password-bearing DDL.
        RAISE_APPLICATION_ERROR(-20002, 'Bootstrap failed (Oracle code ' || SQLCODE
            || '); check account state, password policy and ADMIN privileges.');
END;
/

UNDEFINE owner_password
UNDEFINE reader_password
SET FEEDBACK ON
PROMPT Bootstrap complete. Run migrations as ATHLETE_LEDGER, never as ADMIN.
