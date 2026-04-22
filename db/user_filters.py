"""Developer / administrator account filters.

Users in DEV_ADMIN_USERS are non-end-user accounts (MSTR developers, admins,
BI support) whose activity distorts rationalization signals:
  - Their personal-folder (/Profiles/.../My Reports) reports are dev sandboxes,
    not production artifacts worth rationalizing.
  - Their execution volume is driven by testing/validation, not business use.

Effects when a row matches one of these users:
  - In Heavy Users view: user is flagged `isService = True` (hidden by default).
  - In telemetry aggregation: rows where the user appears AND the folder_path
    starts with the project's Profiles/ prefix are excluded so their personal
    reports don't inherit execution counts from validation runs.

Match is case-insensitive substring match against the "Last, First (region)"
form that appears in UserActivity.csv. The numeric/hyphen suffix variants
("Anthony, Gloria (AS)-1", "Gharpure, Yash (AS)-2") are handled by matching
the name prefix before the " (".
"""
from __future__ import annotations


DEV_ADMIN_USERS: set[str] = {
    "Ramachandrappa, Abhilash",
    "Nivarthi, Sandya",
    "Kolla, Bhanu Manikanta",
    "kuravalapalli, Prathap Reddy",
    "Mekala, Srinivasulu",
    "PisiPati, Ravi Sasanka",
    "Satish, Sreelakshmi",
    "reddy, deekshitha",
    "Kaurase, Jayshree",
    "Anthony, Gloria",
    "Singh, Rangoli",
    "Chavan, Prajakta",
    "Chawade, Sanket",
    "Thekkekara, Tom",
    "Anandhan, Jayshree",
    "Kumar, Santosh",
    "Kumar, Deepak",
    "Basava, Venkatesh",
    "Sasidharan, Suku",
    "A, Venkata Satyanarayana",
    "Lau, Leo",
    "Gharpure, Yash",
}

_DEV_ADMIN_LOWER = {u.lower() for u in DEV_ADMIN_USERS}


def is_dev_admin(user: str) -> bool:
    """Case-insensitive substring match — catches "(US)", "(AS)-1" suffixes."""
    if not user:
        return False
    ul = user.lower()
    return any(u in ul for u in _DEV_ADMIN_LOWER)
