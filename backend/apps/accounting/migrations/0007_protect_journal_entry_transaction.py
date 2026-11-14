
from django.db import migrations


FORWARD_SQL = """
CREATE OR REPLACE FUNCTION prevent_journal_entry_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'Journal entries cannot be deleted.'
            USING ERRCODE = '23514';
    END IF;

    IF pg_trigger_depth() = 2
        AND OLD.posted_at IS NULL
        AND NEW.posted_at IS NOT NULL
        AND OLD.id IS NOT DISTINCT FROM NEW.id
        AND OLD.reference IS NOT DISTINCT FROM NEW.reference
        AND OLD.operation_type IS NOT DISTINCT FROM NEW.operation_type
        AND OLD.transaction_id IS NOT DISTINCT FROM NEW.transaction_id
        AND OLD.currency_id IS NOT DISTINCT FROM NEW.currency_id
        AND OLD.description IS NOT DISTINCT FROM NEW.description
        AND OLD.initiated_by_id IS NOT DISTINCT FROM NEW.initiated_by_id
    THEN
        RETURN NEW;
    END IF;

    RAISE EXCEPTION 'Journal entries are immutable.'
        USING ERRCODE = '23514';
END;
$$;
"""


REVERSE_SQL = """
CREATE OR REPLACE FUNCTION prevent_journal_entry_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'Journal entries cannot be deleted.'
            USING ERRCODE = '23514';
    END IF;

    IF pg_trigger_depth() = 2
        AND OLD.posted_at IS NULL
        AND NEW.posted_at IS NOT NULL
        AND OLD.id IS NOT DISTINCT FROM NEW.id
        AND OLD.reference IS NOT DISTINCT FROM NEW.reference
        AND OLD.operation_type IS NOT DISTINCT FROM NEW.operation_type
        AND OLD.currency_id IS NOT DISTINCT FROM NEW.currency_id
        AND OLD.description IS NOT DISTINCT FROM NEW.description
        AND OLD.initiated_by_id IS NOT DISTINCT FROM NEW.initiated_by_id
    THEN
        RETURN NEW;
    END IF;

    RAISE EXCEPTION 'Journal entries are immutable.'
        USING ERRCODE = '23514';
END;
$$;
"""


class Migration(migrations.Migration):

    dependencies = [
        ("accounting", "0006_journalentry_unique_journal_entry_per_transaction"),
    ]

    operations = [
        migrations.RunSQL(
            sql=FORWARD_SQL,
            reverse_sql=REVERSE_SQL,
        ),
    ]
