# LedgerCore

**LedgerCore** is a backend project focused on building a double-entry accounting ledger and digital wallet API using Django and PostgreSQL.

The project explores practical backend engineering concepts, including relational database design, financial data integrity, transaction safety, and reliable handling of money-moving operations.

**Status:** In development.

## Technology Stack

- Python 3.12
- Django 5.2
- Django REST Framework
- PostgreSQL
- Psycopg 3
- django-environ

## Current Implementation

The initial accounting foundation includes:

- A currency model with configurable minor-unit precision.
- A ledger account model with five accounting account types.
- UUID primary keys for ledger accounts.
- Account codes unique within each currency.
- Protected currency references to preserve historical relationships.
- Derived normal-side logic for debit and credit account balances.

## Planned Development

The next stages include:

1. Journal entries and journal lines.
2. Double-entry accounting validation and immutable posting.
3. Wallet creation and lifecycle management.
4. Atomic transfers and balance calculations.
5. Idempotency and transaction reversals.
6. Deposit and withdrawal processing.
7. Integration tests, Docker, CI/CD, and deployment.

## Local Development

### Prerequisites

- Python 3.12
- PostgreSQL
- Git

### 1. Clone the repository

```bash
git clone https://github.com/abionajoshua1/ledgercore.git
cd ledgercore
```

### 2. Create and activate a virtual environment

On Windows PowerShell:

```powershell
py -3.12 -m venv backend/.venv
backend/.venv/Scripts/Activate.ps1
```

### 3. Install dependencies

```powershell
python -m pip install -r backend/requirements.txt
```

### 4. Configure environment variables

Copy `.env.example` to `.env` in the repository root.

Replace the placeholder values with your own Django secret key and PostgreSQL credentials.

Create a PostgreSQL database and user matching the values in your `.env` file.

### 5. Apply migrations

```powershell
cd backend
python manage.py migrate
```

### 6. Run the development server

```powershell
python manage.py runserver
```

The development server will be available at `http://127.0.0.1:8000/`.

## Running Tests

From the `backend` directory:

```powershell
python manage.py test apps.accounting
```

## Engineering Principles

LedgerCore is being developed around the following principles:

- Store monetary amounts as integer minor units rather than floating-point values.
- Treat PostgreSQL as the authoritative source of financial records.
- Preserve posted accounting history rather than modifying historical transactions.
- Enforce accounting invariants at the appropriate application and database layers.
- Use database transactions and concurrency controls for money-moving operations.
- Make financial operations safe to retry through idempotency mechanisms.

## Project Scope

This is an educational portfolio project under active development. Planned functionality should not be interpreted as already implemented or production-ready.