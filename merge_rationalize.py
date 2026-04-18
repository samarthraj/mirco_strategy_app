import json, csv
from datetime import datetime

# Load inventory
with open('d:/dev/mstr_api/mstr_rationalization_go_full_apr08/inventory/reports.json', 'r', encoding='utf-8') as f:
    inv_reports = json.load(f)

inv_by_name = {}
for r in inv_reports:
    name = r['name'].strip()
    if name not in inv_by_name:
        inv_by_name[name] = []
    inv_by_name[name].append(r)

# Parse full telemetry
telemetry = {}
with open('d:/dev/mstr_api/report-telemetry-go.csv', 'r', encoding='utf-8-sig') as f:
    reader = csv.reader(f)
    header = next(reader)
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
        avg_time = float(row[9].replace(',', '') or '0')
        max_time = float(row[10].replace(',', '') or '0')
        last_exec = row[11].strip()

        if obj not in telemetry:
            telemetry[obj] = {
                'folders': set(), 'users': set(), 'object_type': obj_type,
                'total_executions': 0, 'total_errors': 0,
                'max_avg_time': 0, 'max_max_time': 0, 'last_exec': '',
            }
        telemetry[obj]['folders'].add(folder)
        telemetry[obj]['users'].add(user)
        telemetry[obj]['total_executions'] += execs
        telemetry[obj]['total_errors'] += errors
        telemetry[obj]['max_avg_time'] = max(telemetry[obj]['max_avg_time'], avg_time)
        telemetry[obj]['max_max_time'] = max(telemetry[obj]['max_max_time'], max_time)
        if last_exec > telemetry[obj]['last_exec']:
            telemetry[obj]['last_exec'] = last_exec

# Build merged universe
all_names = set(inv_by_name.keys()) | set(telemetry.keys())

results = []
for name in sorted(all_names):
    in_inv = name in inv_by_name
    in_tel = name in telemetry
    t = telemetry.get(name, {})
    inv_list = inv_by_name.get(name, [])

    folders = t.get('folders', set())
    is_personal = any('/Profiles/' in f for f in folders)
    is_public = any('/Public Objects/' in f or '/Object Templates/' in f for f in folders)
    if not folders and inv_list:
        fp = inv_list[0].get('folderPath', '') or ''
        is_personal = '/Profiles/' in fp
        is_public = '/Public Objects/' in fp or '/Object Templates/' in fp

    if is_public and is_personal:
        location = 'both'
    elif is_public:
        location = 'public'
    elif is_personal:
        location = 'personal'
    else:
        location = 'unknown'

    date_modified = ''
    owner = ''
    if inv_list:
        date_modified = (inv_list[0].get('dateModified', '') or '')[:10]
        owner = inv_list[0].get('owner', '') or ''

    results.append({
        'name': name,
        'in_inventory': in_inv,
        'in_telemetry': in_tel,
        'used': in_tel,
        'location': location,
        'object_type': t.get('object_type', ''),
        'total_executions': t.get('total_executions', 0),
        'total_errors': t.get('total_errors', 0),
        'unique_users': len(t.get('users', set())),
        'last_exec': t.get('last_exec', ''),
        'max_avg_time_s': t.get('max_avg_time', 0),
        'max_max_time_s': t.get('max_max_time', 0),
        'date_modified': date_modified,
        'owner': owner,
        'inventory_copies': len(inv_list),
        'folders': '; '.join(sorted(t.get('folders', set()) or set())),
    })

# Summary
used = [r for r in results if r['used']]
unused = [r for r in results if not r['used']]
used_public = [r for r in used if r['location'] in ('public', 'both')]
used_personal = [r for r in used if r['location'] == 'personal']
unused_public = [r for r in unused if r['location'] in ('public', 'unknown')]
unused_personal = [r for r in unused if r['location'] == 'personal']

print("=== GLOBAL OPERATIONAL - MERGED RATIONALIZATION ===")
print("Sources: inventory (API extract Apr 9) + telemetry (Aug 31 2025 - Apr 9 2026)")
print("")
print(f"Total unique report names:  {len(all_names)}")
print(f"  From inventory only:      {sum(1 for r in results if r['in_inventory'] and not r['in_telemetry'])}")
print(f"  From telemetry only:      {sum(1 for r in results if r['in_telemetry'] and not r['in_inventory'])}")
print(f"  In both sources:          {sum(1 for r in results if r['in_inventory'] and r['in_telemetry'])}")
print("")
print(f"USED (has telemetry):       {len(used)} ({len(used)/len(results)*100:.1f}%)")
print(f"  Public/shared:            {len(used_public)}")
print(f"  Personal (My Reports):    {len(used_personal)}")
print(f"UNUSED (no telemetry):      {len(unused)} ({len(unused)/len(results)*100:.1f}%)")
print(f"  Public/shared:            {len(unused_public)}")
print(f"  Personal (My Reports):    {len(unused_personal)}")

# Unused by age
now = datetime(2026, 4, 9)
age_buckets = {
    '< 1 year': 0, '1-2 years': 0, '2-3 years': 0,
    '3-5 years': 0, '5+ years': 0, 'no date (not in inventory)': 0
}
for r in unused:
    dm = r['date_modified']
    if dm:
        try:
            dt = datetime.strptime(dm, '%Y-%m-%d')
            age = (now - dt).days
            if age < 365: age_buckets['< 1 year'] += 1
            elif age < 730: age_buckets['1-2 years'] += 1
            elif age < 1095: age_buckets['2-3 years'] += 1
            elif age < 1825: age_buckets['3-5 years'] += 1
            else: age_buckets['5+ years'] += 1
        except:
            age_buckets['no date (not in inventory)'] += 1
    else:
        age_buckets['no date (not in inventory)'] += 1

print(f"\nUNUSED by age (inventory dateModified):")
for bucket, count in age_buckets.items():
    print(f"  {bucket}: {count}")

# Top used
print(f"\nTOP 20 MOST EXECUTED REPORTS:")
for r in sorted(used, key=lambda x: -x['total_executions'])[:20]:
    loc = 'PUB' if r['location'] in ('public','both') else 'PER'
    err_rate = f"{r['total_errors']/r['total_executions']*100:.0f}%" if r['total_executions'] > 0 else '0%'
    print(f"  {r['total_executions']:>7,} execs | {r['unique_users']:>3} users | err {err_rate:>4} | {loc} | {r['name']}")

# Save
output = {
    'summary': {
        'project': 'Global Operational',
        'telemetry_window': 'Aug 31, 2025 - Apr 9, 2026',
        'total_unique_reports': len(all_names),
        'from_inventory_only': sum(1 for r in results if r['in_inventory'] and not r['in_telemetry']),
        'from_telemetry_only': sum(1 for r in results if r['in_telemetry'] and not r['in_inventory']),
        'in_both': sum(1 for r in results if r['in_inventory'] and r['in_telemetry']),
        'used': len(used),
        'unused': len(unused),
        'used_pct': round(len(used)/len(results)*100, 1),
        'used_public': len(used_public),
        'used_personal': len(used_personal),
        'unused_public': len(unused_public),
        'unused_personal': len(unused_personal),
        'unused_by_age': age_buckets,
    },
    'reports': results,
}

with open('d:/dev/mstr_api/go_usage_rationalization.json', 'w') as f:
    json.dump(output, f, indent=2)

print(f"\nSaved {len(results)} reports to go_usage_rationalization.json")
