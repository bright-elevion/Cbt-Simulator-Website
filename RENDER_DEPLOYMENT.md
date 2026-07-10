# Deploying to Render with Persistent Storage

To ensure that your users' payment data and scores are not lost when you update your code on Render, you **must** use a Persistent Disk. Render's default filesystem is "ephemeral," meaning it resets every time you deploy.

### Steps to set up Persistent Storage on Render:

1.  **Go to your Web Service Dashboard** on Render.
2.  **Scroll down to the "Disks" section**.
3.  **Click "Add Disk"**.
4.  **Configure the Disk:**
    *   **Name:** `cbt_database`
    *   **Mount Path:** `/home/ubuntu/cbt-simulator/database` (or simply `database` if your root directory is different)
    *   **Size:** `1 GB` (This is the minimum and more than enough for SQLite)
5.  **Environment Variable:**
    *   Ensure your `DB_PATH` in `app.py` correctly points to the mounted disk. The current code uses `os.path.join(os.path.dirname(__file__), 'database', 'quiz.db')`, which will work perfectly if you mount the disk to the `database` folder in your project root.

### Why this fixes the "Paying 500 again" issue:
-   **Old behavior:** Every time you pushed code, Render deleted the old `quiz.db` and created a new one from your `init_db.py` script.
-   **New behavior:** The `quiz.db` file will live on the Persistent Disk. When you redeploy, Render unmounts the disk from the old version and mounts it to the new version. Your data remains untouched.
-   **Init Logic:** I have updated `init_db.py` to use `CREATE TABLE IF NOT EXISTS`. This means it will only create the tables the very first time. On subsequent updates, it will see the tables already exist and leave your data alone.
