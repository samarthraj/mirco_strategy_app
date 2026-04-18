import json, csv
from datetime import datetime
from collections import defaultdict

NOW = datetime(2026, 4, 9)
CUTOFF_MONTHS = 8
CUTOFF_DATE = datetime(2025, 8, 9)
TELEMETRY_START = "2025-08-31"
TELEMETRY_END = "2026-04-09"

# ── Load inventory ──────────────────────────────────────────────────────────
with open('d:/dev/mstr_api/mstr_rationalization_go_full_apr08/inventory/reports.json', 'r', encoding='utf-8') as f:
    inv_reports = json.load(f)

inv_by_name = {}
for r in inv_reports:
    name = r['name'].strip()
    if name not in inv_by_name:
        inv_by_name[name] = []
    inv_by_name[name].append(r)

# ── Load existing rationalization report ────────────────────────────────────
with open('d:/dev/mstr_api/mstr_rationalization_go_full_apr08/rationalization_report.json', 'r') as f:
    existing_report = json.load(f)

# ── Parse telemetry ─────────────────────────────────────────────────────────
telemetry = {}
with open('d:/dev/mstr_api/report-telemetry-go.csv', 'r', encoding='utf-8-sig') as f:
    reader = csv.reader(f)
    next(reader)
    last_obj = None
    for row in reader:
        obj = row[0].strip() if row[0].strip() else last_obj
        if row[0].strip():
            last_obj = obj
        if not obj:
            continue
        folder = row[1].strip()
        user = row[2].strip()
        obj_type = row[3].strip()
        execs = int(row[5].replace(',', '') or '0')
        errors = int(row[7].replace(',', '') or '0')
        err_rate = row[8].strip()
        avg_time = float(row[9].replace(',', '') or '0')
        max_time = float(row[10].replace(',', '') or '0')
        last_exec = row[11].strip()

        if obj not in telemetry:
            telemetry[obj] = {
                'folders': set(), 'users': set(), 'object_type': obj_type,
                'total_executions': 0, 'total_errors': 0,
                'max_avg_time': 0, 'max_max_time': 0, 'last_exec': '',
                'user_exec_detail': []
            }
        telemetry[obj]['folders'].add(folder)
        telemetry[obj]['users'].add(user)
        telemetry[obj]['total_executions'] += execs
        telemetry[obj]['total_errors'] += errors
        telemetry[obj]['max_avg_time'] = max(telemetry[obj]['max_avg_time'], avg_time)
        telemetry[obj]['max_max_time'] = max(telemetry[obj]['max_max_time'], max_time)
        if last_exec > telemetry[obj]['last_exec']:
            telemetry[obj]['last_exec'] = last_exec
        telemetry[obj]['user_exec_detail'].append({
            'user': user, 'executions': execs, 'errors': errors,
            'avg_time_s': avg_time, 'last_exec': last_exec, 'folder': folder
        })

# ── Merge into full universe ────────────────────────────────────────────────
all_names = set(inv_by_name.keys()) | set(telemetry.keys())

def classify_location(folders, inv_list):
    is_personal = any('/Profiles/' in f for f in folders)
    is_public = any('/Public Objects/' in f or '/Object Templates/' in f for f in folders)
    if not folders and inv_list:
        fp = inv_list[0].get('folderPath', '') or ''
        is_personal = '/Profiles/' in fp
        is_public = '/Public Objects/' in fp or '/Object Templates/' in fp
    if is_public and is_personal:
        return 'both'
    elif is_public:
        return 'public'
    elif is_personal:
        return 'personal'
    return 'unknown'

def get_age_bucket(date_str):
    if not date_str:
        return 'unknown'
    try:
        dt = datetime.strptime(date_str[:10], '%Y-%m-%d')
        days = (NOW - dt).days
        if days < 365: return '< 1 year'
        elif days < 730: return '1-2 years'
        elif days < 1095: return '2-3 years'
        elif days < 1825: return '3-5 years'
        else: return '5+ years'
    except:
        return 'unknown'

keep_list = []
retire_list = []
all_reports = []

for name in sorted(all_names):
    in_inv = name in inv_by_name
    in_tel = name in telemetry
    t = telemetry.get(name, {})
    inv_list = inv_by_name.get(name, [])

    folders = t.get('folders', set())
    location = classify_location(folders, inv_list)

    date_modified = ''
    date_created = ''
    owner = ''
    report_id = ''
    folder_path = ''
    if inv_list:
        date_modified = (inv_list[0].get('dateModified', '') or '')[:10]
        date_created = (inv_list[0].get('dateCreated', '') or '')[:10]
        owner = inv_list[0].get('owner', '') or ''
        report_id = inv_list[0].get('id', '')
        folder_path = inv_list[0].get('folderPath', '') or ''
    if not folder_path and folders:
        folder_path = sorted(folders)[0]

    last_exec_str = t.get('last_exec', '')
    total_execs = t.get('total_executions', 0)
    total_errors = t.get('total_errors', 0)
    unique_users = len(t.get('users', set()))
    error_rate = f"{total_errors/total_execs*100:.1f}%" if total_execs > 0 else 'N/A'

    entry = {
        'name': name,
        'id': report_id,
        'location': location,
        'folder_path': folder_path,
        'owner': owner,
        'object_type': t.get('object_type', 'Grid Report'),
        'in_inventory': in_inv,
        'in_telemetry': in_tel,
        'date_created': date_created,
        'date_modified': date_modified,
        'age_bucket': get_age_bucket(date_modified),
        'total_executions': total_execs,
        'total_errors': total_errors,
        'error_rate': error_rate,
        'unique_users': unique_users,
        'last_exec': last_exec_str,
        'max_avg_time_s': t.get('max_avg_time', 0),
        'max_max_time_s': t.get('max_max_time', 0),
        'inventory_copies': len(inv_list),
    }

    if in_tel:
        entry['action'] = 'KEEP'
        entry['reason'] = f'Active - {total_execs} executions by {unique_users} user(s), last exec {last_exec_str}'
        keep_list.append(entry)
    else:
        entry['action'] = 'RETIRE'
        entry['reason'] = f'Zero executions in {CUTOFF_MONTHS}-month telemetry window ({TELEMETRY_START} to {TELEMETRY_END})'
        if date_modified:
            entry['reason'] += f'; last modified {date_modified} ({get_age_bucket(date_modified)})'
        retire_list.append(entry)

    all_reports.append(entry)

# ── Retirement priority tiers ───────────────────────────────────────────────
tier1 = []  # 5+ years, no usage - immediate retire
tier2 = []  # 3-5 years, no usage - retire with notification
tier3 = []  # 1-3 years, no usage - review with owners
tier4 = []  # < 1 year, no usage - investigate (might be new/seasonal)

for r in retire_list:
    ab = r['age_bucket']
    if ab == '5+ years':
        r['retirement_tier'] = 1
        r['tier_label'] = 'Tier 1 - Immediate retirement (5+ years stale, zero usage)'
        tier1.append(r)
    elif ab == '3-5 years':
        r['retirement_tier'] = 2
        r['tier_label'] = 'Tier 2 - Retire with notification (3-5 years stale, zero usage)'
        tier2.append(r)
    elif ab in ('2-3 years', '1-2 years'):
        r['retirement_tier'] = 3
        r['tier_label'] = 'Tier 3 - Review with owners (1-3 years stale, zero usage)'
        tier3.append(r)
    elif ab == '< 1 year':
        r['retirement_tier'] = 4
        r['tier_label'] = 'Tier 4 - Investigate (< 1 year old, zero usage - may be new/seasonal)'
        tier4.append(r)
    else:
        r['retirement_tier'] = 3
        r['tier_label'] = 'Tier 3 - Review (no date info available)'
        tier3.append(r)

# ── Aggregations ────────────────────────────────────────────────────────────
retire_by_owner = defaultdict(list)
for r in retire_list:
    retire_by_owner[r['owner'] or 'unknown'].append(r['name'])

retire_by_folder = defaultdict(int)
for r in retire_list:
    fp = r['folder_path']
    parts = fp.split('/')
    short = '/'.join(parts[:5]) if len(parts) > 5 else fp
    retire_by_folder[short] += 1

keep_by_owner = defaultdict(list)
for r in keep_list:
    folders = telemetry.get(r['name'], {}).get('folders', set())
    for f in folders:
        if '/Profiles/' in f:
            profile = f.split('/Profiles/')[1].split('/')[0]
            keep_by_owner[profile].append(r['name'])

# ── Build integrated report ─────────────────────────────────────────────────
report = {
    "metadata": {
        "project": "Global Operational",
        "project_id": "E77B77894C04BF0E6D244F9363CFAF64",
        "generated_at": NOW.isoformat(),
        "telemetry_window": f"{TELEMETRY_START} to {TELEMETRY_END}",
        "usage_cutoff_months": CUTOFF_MONTHS,
        "cutoff_date": CUTOFF_DATE.strftime('%Y-%m-%d'),
        "sources": [
            "MicroStrategy REST API inventory extract (Apr 9, 2026)",
            "MicroStrategy telemetry/usage data (Aug 31, 2025 - Apr 9, 2026)"
        ]
    },
    "summary": {
        "total_unique_reports": len(all_names),
        "from_inventory_only": sum(1 for r in all_reports if r['in_inventory'] and not r['in_telemetry']),
        "from_telemetry_only": sum(1 for r in all_reports if r['in_telemetry'] and not r['in_inventory']),
        "in_both_sources": sum(1 for r in all_reports if r['in_inventory'] and r['in_telemetry']),
        "keep": {
            "total": len(keep_list),
            "public": sum(1 for r in keep_list if r['location'] in ('public', 'both')),
            "personal": sum(1 for r in keep_list if r['location'] == 'personal'),
            "pct": round(len(keep_list) / len(all_reports) * 100, 1),
        },
        "retire": {
            "total": len(retire_list),
            "public": sum(1 for r in retire_list if r['location'] in ('public', 'both', 'unknown')),
            "personal": sum(1 for r in retire_list if r['location'] == 'personal'),
            "pct": round(len(retire_list) / len(all_reports) * 100, 1),
            "by_tier": {
                "tier1_immediate": len(tier1),
                "tier2_notify": len(tier2),
                "tier3_review": len(tier3),
                "tier4_investigate": len(tier4),
            },
            "by_age": {
                '5+ years': sum(1 for r in retire_list if r['age_bucket'] == '5+ years'),
                '3-5 years': sum(1 for r in retire_list if r['age_bucket'] == '3-5 years'),
                '2-3 years': sum(1 for r in retire_list if r['age_bucket'] == '2-3 years'),
                '1-2 years': sum(1 for r in retire_list if r['age_bucket'] == '1-2 years'),
                '< 1 year': sum(1 for r in retire_list if r['age_bucket'] == '< 1 year'),
                'unknown': sum(1 for r in retire_list if r['age_bucket'] == 'unknown'),
            },
            "by_location": {
                'public': sum(1 for r in retire_list if r['location'] == 'public'),
                'personal': sum(1 for r in retire_list if r['location'] == 'personal'),
                'both': sum(1 for r in retire_list if r['location'] == 'both'),
                'unknown': sum(1 for r in retire_list if r['location'] == 'unknown'),
            },
            "top_folders": dict(sorted(retire_by_folder.items(), key=lambda x: -x[1])[:15]),
            "top_owners": {k: len(v) for k, v in sorted(retire_by_owner.items(), key=lambda x: -len(x[1]))[:20]},
        },
        "existing_rationalization": existing_report.get("summary", {}),
    },
    "retirement_action_list": {
        "tier1_immediate_retirement": {
            "description": "Reports last modified 5+ years ago with zero executions in 8-month telemetry window. Safe to archive/delete immediately.",
            "count": len(tier1),
            "reports": tier1,
        },
        "tier2_retire_with_notification": {
            "description": "Reports last modified 3-5 years ago with zero executions. Notify owners before retirement (2-week grace period).",
            "count": len(tier2),
            "reports": tier2,
        },
        "tier3_review_with_owners": {
            "description": "Reports last modified 1-3 years ago with zero executions. May have seasonal or periodic use not captured. Review with owners.",
            "count": len(tier3),
            "reports": tier3,
        },
        "tier4_investigate": {
            "description": "Reports less than 1 year old with zero executions. May be newly created, under development, or seasonal. Investigate before action.",
            "count": len(tier4),
            "reports": tier4,
        },
    },
    "active_reports": {
        "description": f"Reports with at least 1 execution in the {CUTOFF_MONTHS}-month telemetry window.",
        "count": len(keep_list),
        "reports": keep_list,
    },
    "personal_report_owners": {
        k: sorted(set(v)) for k, v in sorted(keep_by_owner.items(), key=lambda x: -len(x[1]))
    },
}

# Save
with open('d:/dev/mstr_api/go_usage_rationalization.json', 'w') as f:
    json.dump(report, f, indent=2)

# ── Print summary ───────────────────────────────────────────────────────────
print("=" * 70)
print("GLOBAL OPERATIONAL - RATIONALIZATION & RETIREMENT REPORT")
print("=" * 70)
print(f"Telemetry: {TELEMETRY_START} to {TELEMETRY_END} ({CUTOFF_MONTHS}-month window)")
print(f"Generated: {NOW.strftime('%Y-%m-%d')}")
print()
print(f"{'Total unique reports:':<35} {len(all_names):>6}")
print(f"  {'Inventory only:':<33} {report['summary']['from_inventory_only']:>6}")
print(f"  {'Telemetry only:':<33} {report['summary']['from_telemetry_only']:>6}")
print(f"  {'Both sources:':<33} {report['summary']['in_both_sources']:>6}")
print()
s = report['summary']
print(f"{'KEEP (active):':<35} {s['keep']['total']:>6} ({s['keep']['pct']}%)")
print(f"  {'Public/shared:':<33} {s['keep']['public']:>6}")
print(f"  {'Personal:':<33} {s['keep']['personal']:>6}")
print()
print(f"{'RETIRE (inactive):':<35} {s['retire']['total']:>6} ({s['retire']['pct']}%)")
print(f"  {'Public/shared:':<33} {s['retire']['public']:>6}")
print(f"  {'Personal:':<33} {s['retire']['personal']:>6}")
print()
print("RETIREMENT TIERS:")
rt = s['retire']['by_tier']
print(f"  Tier 1 - Immediate (5+ yr stale):  {rt['tier1_immediate']:>6}")
print(f"  Tier 2 - Notify (3-5 yr stale):    {rt['tier2_notify']:>6}")
print(f"  Tier 3 - Review (1-3 yr stale):    {rt['tier3_review']:>6}")
print(f"  Tier 4 - Investigate (< 1 yr):     {rt['tier4_investigate']:>6}")
print()
print("TOP FOLDERS WITH RETIRE CANDIDATES:")
for folder, count in list(report['summary']['retire']['top_folders'].items())[:10]:
    print(f"  {count:>4} | {folder}")
print()
print("TOP OWNERS OF RETIRE CANDIDATES:")
for owner, count in list(report['summary']['retire']['top_owners'].items())[:10]:
    print(f"  {count:>4} | {owner}")
print()
print(f"Saved to go_usage_rationalization.json")
