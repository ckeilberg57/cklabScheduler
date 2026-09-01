# Microsoft Entra ID Authentication

This guide walks an Azure administrator through registering cklabScheduler as an app in Microsoft Entra ID (Azure Active Directory) and enabling OIDC/OAuth2 single-sign-on.

---

## Prerequisites

- An Azure tenant where you have **Application Administrator** (or Global Administrator) privileges
- The public URL where cklabScheduler is accessible (e.g. `https://scheduler.example.com/cklabScheduler`)
- cklabScheduler installed and accessible via HTTPS — the redirect URI must be reachable from Entra

---

## Step 1 — Register the application

1. Go to the [Azure portal](https://portal.azure.com) and navigate to **Azure Active Directory → App registrations**.
2. Click **New registration**.
3. Set:
   - **Name**: `cklabScheduler` (or any name meaningful to your organisation)
   - **Supported account types**: **Accounts in this organizational directory only** (single-tenant)
   - **Redirect URI**: leave blank for now — you will add it in Step 2
4. Click **Register**.
5. Note the **Application (client) ID** and **Directory (tenant) ID** — you will need both during installation.

---

## Step 2 — Add the redirect URI

1. On the app registration page, go to **Authentication → Add a platform → Web**.
2. Set the **Redirect URI** to:
   ```
   https://<your-server-hostname>/cklabScheduler/auth/callback
   ```
3. Under **Advanced settings**, leave **Implicit grant** unchecked (Authorization Code Flow does not use implicit grant).
4. Click **Configure**.

---

## Step 3 — Create a client secret

1. Go to **Certificates & secrets → Client secrets → New client secret**.
2. Set a description (e.g. `cklabScheduler production`) and an expiry period.
3. Click **Add**.
4. Copy the secret **Value** immediately — it is not shown again after you leave the page.

> The client secret goes into `ENTRA_CLIENT_SECRET` during installation. Store it securely and rotate it before expiry.

---

## Step 4 — Define app roles

cklabScheduler uses Entra app roles to assign permissions. You must create two roles:

1. Go to **App roles → Create app role**.

   **Role 1 — Administrator:**
   - Display name: `Scheduler Administrator`
   - Allowed member types: **Users/Groups**
   - Value: `Scheduler.Administrator`
   - Description: `Full access to cklabScheduler, including managing meetings and settings.`
   - Click **Apply**.

   **Role 2 — User:**
   - Display name: `Scheduler User`
   - Allowed member types: **Users/Groups**
   - Value: `Scheduler.User`
   - Description: `Standard access to create and manage meetings in cklabScheduler.`
   - Click **Apply**.

---

## Step 5 — Assign roles to users or groups

1. Go to **Azure Active Directory → Enterprise Applications** and find the application you just registered.
2. Go to **Users and groups → Add user/group**.
3. Select the users or groups to grant access, then select the appropriate role (`Scheduler Administrator` or `Scheduler User`).
4. Click **Assign**.

> Users without a role assignment will be denied access when they attempt to sign in.

---

## Step 6 — Configure the token claim (optional — role claim verification)

By default, Entra includes app role assignments in the `roles` claim of the ID token. No additional configuration is needed. You can verify this by checking **Token configuration** on the app registration — `roles` should be present under ID token claims. If it is not listed, add it:

1. Go to **Token configuration → Add optional claim → ID**.
2. Select `roles` and click **Add**.

---

## Step 7 — Record the configuration values

At the end of registration, you should have:

| Value | Where to find it |
|---|---|
| **Tenant ID** | App registration overview → Directory (tenant) ID |
| **Client ID** | App registration overview → Application (client) ID |
| **Client secret** | Saved from Step 3 |
| **Redirect URI** | `https://<hostname>/cklabScheduler/auth/callback` |
| **Post-logout URI** | `https://<hostname>/cklabScheduler/login` |

---

## Step 8 — Run the installer (or edit the env file)

**During fresh installation** (`deploy/install.sh`), answer `Y` when asked about Entra and enter the values above.

**On an existing installation**, edit `/etc/cklabScheduler/cklabScheduler.env` and add:

```
LOCAL_AUTH_ENABLED="true"
ENTRA_ENABLED="true"
ENTRA_TENANT_ID="<your-tenant-id>"
ENTRA_CLIENT_ID="<your-client-id>"
ENTRA_CLIENT_SECRET="<your-client-secret>"
ENTRA_AUTHORITY="https://login.microsoftonline.com/<your-tenant-id>"
ENTRA_REDIRECT_URI="https://<hostname>/cklabScheduler/auth/callback"
ENTRA_POST_LOGOUT_REDIRECT_URI="https://<hostname>/cklabScheduler/login"
```

Then restart the web service:

```bash
systemctl restart cklab-scheduler-web
```

---

## Role mapping

| Entra app role claim | cklabScheduler role |
|---|---|
| `Scheduler.Administrator` | `administrator` — full access |
| `Scheduler.User` | `scheduler_user` — standard access |
| No role assigned | Access denied |

Users without any matching role assignment receive a 403 Access Denied response after authentication.

---

## Sign-in flow

1. User visits the application and clicks **Sign in with Microsoft**.
2. The browser is redirected to `https://login.microsoftonline.com/<tenant>/oauth2/v2.0/authorize`.
3. User authenticates with their Microsoft credentials (MFA is enforced by Entra, not by cklabScheduler).
4. Entra redirects back to `/auth/callback` with an authorization code.
5. cklabScheduler exchanges the code for an ID token and extracts the user's role from the `roles` claim.
6. A session is created; the user is redirected to the application.

---

## Troubleshooting

**"AADSTS700016: Application not found"**
- The `ENTRA_CLIENT_ID` is incorrect or the app is registered in the wrong tenant.

**"AADSTS50011: Reply URL does not match"**
- The redirect URI configured in Azure does not exactly match `ENTRA_REDIRECT_URI` in the env file, including protocol, hostname, and path.

**User authenticated but sees "Access Denied"**
- The user has no app role assigned in **Enterprise Applications → Users and groups**.
- Check that the role value is exactly `Scheduler.Administrator` or `Scheduler.User` (case-sensitive).

**Client secret error at startup**
- The secret may have expired. Create a new one in Azure and update `ENTRA_CLIENT_SECRET`, then `systemctl restart cklab-scheduler-web`.

---

## Security notes

- cklabScheduler uses the **Authorization Code Flow** — it never handles Microsoft usernames or passwords.
- MFA is enforced by Entra (or Conditional Access), not by this application.
- ID tokens and access tokens are never logged. Only the user's display name, email, and role are stored in the SQLite user record.
- The client secret is stored in the env file with `640 root:cklabscheduler` permissions.
- cklabScheduler is registered as a **single-tenant** application. Users from other directories cannot sign in.

---

## Rotating the client secret

1. In Azure, create a new client secret before the existing one expires.
2. Update `ENTRA_CLIENT_SECRET` in `/etc/cklabScheduler/cklabScheduler.env`.
3. Run `systemctl restart cklab-scheduler-web`.
4. Verify sign-in works, then delete the old secret in Azure.
