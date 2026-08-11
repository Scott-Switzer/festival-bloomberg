# GitHub Repository Setup Guide

## Step 1: Create Private GitHub Repository

**You need to do this manually through GitHub's interface:**

1. Go to https://github.com/new
2. Repository name: `festival-intelligence` (or your preferred name)
3. Set to **Private**
4. Don't initialize with README, .gitignore, or license (we have these already)
5. Click "Create repository"

**After creation, GitHub will show you commands like:**
```
git remote add origin https://github.com/YOUR_USERNAME/festival-intelligence.git
```

Copy the URL from GitHub (either HTTPS or SSH).

## Step 2: Initialize Git Repository

Run these commands in the project directory:

```bash
cd /Users/scottthomasswitzer/CascadeProjects/festival-intelligence
git init
git add .
git commit -m "Initial commit: Festival Bloomberg implementation"
```

## Step 3: Add Remote and Push

Replace `YOUR_USERNAME` with your GitHub username and use the URL you copied:

```bash
git remote add origin https://github.com/YOUR_USERNAME/festival-intelligence.git
git branch -M main
git push -u origin main
```

**Or if using SSH:**
```bash
git remote add origin git@github.com:YOUR_USERNAME/festival-intelligence.git
git branch -M main
git push -u origin main
```

## Step 4: Verify

Check that everything is pushed:
```bash
git status
```

Should show: "Your branch is up to date with 'origin/main'."

## Next Steps After GitHub Setup

1. **Configure Cloudflare R2 credentials** - Still needed for object storage
2. **Set up Alembic database migrations** - For schema management
3. **Run initial database migration** - Create Festival Bloomberg tables
4. **Functional testing** - Test core components with real data
5. **Deployment documentation** - Create deployment guide

## Security Notes

✅ `.gitignore` is already configured to exclude:
- `.env` file (contains API keys)
- `venv/` (virtual environment)
- `.pyc` files
- IDE files
- Data files

✅ Your NVIDIA API key is in `.env` and will not be committed

⚠️ **Never commit:**
- API keys
- Database credentials
- Production secrets
