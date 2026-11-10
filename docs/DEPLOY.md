# Deploying the ChangeGuard prototype

The prototype is a Streamlit app (`app.py`). It deploys free on **Streamlit Community Cloud** straight from this
GitHub repository. Amazon Nova calls go to Amazon Bedrock with a key that can call Nova and nothing else.

## 1. A separate, Nova-only AWS key for the website

Use a new IAM user for the site (for example `changeguard-web`), not your personal key, so it can be
switched off on its own after the event.

1. IAM → Users → Create user → `changeguard-web` (no console access).
2. Attach an inline policy that allows only Nova:

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
    "Resource": [
      "arn:aws:bedrock:*::foundation-model/amazon.nova-*",
      "arn:aws:bedrock:*:*:inference-profile/*amazon.nova-*"
    ]
  }]
}
```

3. Security credentials → Create access key → "Application running outside AWS". Keep the CSV private.
4. Optional but recommended: Billing → Budgets → a monthly cost budget of a few dollars with an email alert.

## 2. Deploy on Streamlit Community Cloud

1. Go to https://share.streamlit.io and sign in with GitHub.
2. **Create app** → "Deploy a public app from GitHub".
3. Repository `lokesh-18a/changeguard`, branch `main`, main file `app.py`.
4. App URL: `changeguard-flarre` (gives https://changeguard-flarre.streamlit.app).
5. **Advanced settings** → Python 3.12, and paste into **Secrets**:

```toml
AWS_ACCESS_KEY_ID = "your-access-key-id"
AWS_SECRET_ACCESS_KEY = "your-secret-access-key"
AWS_REGION = "us-east-1"
```

6. Deploy. The first build takes a few minutes. Every later `git push` redeploys automatically.

Without secrets the site still works and uses the rule-based parser (the toggle in the sidebar is disabled).

## Safety on a public site

- Keys are never in the repository; `.streamlit/secrets.toml` is in `.gitignore`.
- Nova runs are capped at 10 per visitor session and 300 per day, and requests at 600 characters.
- Delete or deactivate the `changeguard-web` key after the event.
