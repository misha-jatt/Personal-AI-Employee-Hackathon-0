# Privacy Policy

**Last Updated:** 2026-03-09

## Overview

Personal AI Employee ("the App") is a local-first automation tool. It does not store, sell, or share personal data with third parties.

## Data Collection

The App accesses the following data only when explicitly authorized by the user:

- **LinkedIn data**: Profile information and post creation capabilities via OAuth 2.0
- **Gmail data**: Email metadata for automation tasks via Google OAuth 2.0

## Data Storage

- All OAuth tokens and credentials are stored **locally on your machine** in `.env` files.
- Automation logs are stored locally in the `/Logs` directory.
- **No data is sent to external servers** beyond the authorized API calls to LinkedIn and Gmail.

## Data Sharing

We do **not** sell, share, or transfer your personal data to any third party.

## Data Retention

- OAuth tokens are stored locally until you revoke them or delete the application.
- Audit logs are retained locally for operational purposes.

## User Rights

- You may revoke access at any time by removing your OAuth tokens from the `.env` file.
- You may request data deletion by following the instructions in [USER_DATA_DELETION.md](watchers/USER_DATA_DELETION.md).

## Security

- Credentials are never stored in source code or markdown files.
- All sensitive data is excluded from version control via `.gitignore`.

## Contact

For questions or concerns, contact: **mishajatt960@gmail.com**
