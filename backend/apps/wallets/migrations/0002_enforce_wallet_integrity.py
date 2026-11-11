from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("wallets", "0001_initial"),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
                ALTER TABLE wallets_wallet
                ADD CONSTRAINT wallet_ledger_account_currency_fk
                FOREIGN KEY (ledger_account_id, currency_id)
                REFERENCES accounting_ledgeraccount (id, currency_id);


                CREATE OR REPLACE FUNCTION enforce_wallet_ledger_account_type()
                RETURNS trigger
                LANGUAGE plpgsql
                AS $$
                DECLARE
                    linked_account_type varchar(20);
                BEGIN
                    IF TG_TABLE_NAME = 'wallets_wallet' THEN

                        SELECT account_type
                        INTO linked_account_type
                        FROM accounting_ledgeraccount
                        WHERE id = NEW.ledger_account_id
                        FOR SHARE;

                        IF NOT FOUND THEN
                            RAISE EXCEPTION
                                'Ledger account % does not exist.',
                                NEW.ledger_account_id
                                USING ERRCODE = '23503';
                        END IF;

                        IF linked_account_type <> 'LIABILITY' THEN
                            RAISE EXCEPTION
                                'Wallets must reference LIABILITY ledger accounts.'
                                USING ERRCODE = '23514';
                        END IF;

                    ELSIF TG_TABLE_NAME = 'accounting_ledgeraccount' THEN

                        IF NEW.account_type IS DISTINCT FROM OLD.account_type
                            AND NEW.account_type <> 'LIABILITY'
                            AND EXISTS (
                                SELECT 1
                                FROM wallets_wallet
                                WHERE ledger_account_id = NEW.id
                            )
                        THEN
                            RAISE EXCEPTION
                                'Cannot change the account type of a ledger account linked to a wallet.'
                                USING ERRCODE = '23514';
                        END IF;

                    END IF;

                    RETURN NEW;
                END;
                $$;


                CREATE TRIGGER wallet_require_liability_account
                BEFORE INSERT OR UPDATE ON wallets_wallet
                FOR EACH ROW
                EXECUTE FUNCTION enforce_wallet_ledger_account_type();


                CREATE TRIGGER ledger_account_preserve_wallet_liability
                BEFORE UPDATE OF account_type ON accounting_ledgeraccount
                FOR EACH ROW
                EXECUTE FUNCTION enforce_wallet_ledger_account_type();
            """,
            reverse_sql="""
                DROP TRIGGER ledger_account_preserve_wallet_liability
                ON accounting_ledgeraccount;

                DROP TRIGGER wallet_require_liability_account
                ON wallets_wallet;

                DROP FUNCTION enforce_wallet_ledger_account_type();

                ALTER TABLE wallets_wallet
                DROP CONSTRAINT wallet_ledger_account_currency_fk;
            """,
        ),
    ]