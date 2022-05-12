from collections import defaultdict
from typing import Any
import constants

HIT_TYPE = ["spells_hit", "spells_crit", "dot_hit", "dot_crit"]

def group_targets(targets: set[str]):
    target_ids = {guid[:-6] for guid in targets}
    return {
        target_id: {guid for guid in targets if target_id in guid}
        for target_id in target_ids
    }

PERIODIC = {'SPELL_PERIODIC_DAMAGE', 'SPELL_PERIODIC_ENERGIZE', 'SPELL_PERIODIC_HEAL', 'SPELL_PERIODIC_LEECH', 'SPELL_PERIODIC_MISSED'}

@constants.running_time
def parse_logs(logs_slice: list[str], filter_guids: set[str], mainGUID: str, target_filter=None) -> dict[str, Any]:
    '''absolute = { spell_id: sum }
    useful = { spell_id: {
        "spells_hit": [],
        "spells_crit": [],
        "dot_hit": [],
        "dot_crit": [] } }'''
    targets = set()

    absolute = defaultdict(int)
    useful: dict[int, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))

    print(f"[parse_logs] {mainGUID=} {target_filter=}")

    for line in logs_slice:
        if "DAMAGE" not in line:
            continue
        try:
            _, flag, sGUID, _, tGUID, _, sp_id, _, _, dmg, over, _, res, _, absrb, crit, _ = line.split(',', 16)
        except ValueError:
            # damage_shield_missed
            continue
        if flag == "DAMAGE_SPLIT":
            continue
        if sGUID not in filter_guids:
            continue

        targets.add(tGUID)

        if target_filter:
            if target_filter not in tGUID:
                continue
        elif tGUID in filter_guids:
            continue
        
        dmg_raw = int(dmg)
        spell_id = int(sp_id) if sGUID == mainGUID else -int(sp_id)

        dmg_actual = dmg_raw - int(over)
        _hit_type = (flag in PERIODIC) * 2 + (crit == "1")
        useful[spell_id][HIT_TYPE[_hit_type]].append(dmg_actual)
        
        dmg_absolute = dmg_raw + int(res) + int(absrb)
        absolute[spell_id] += dmg_absolute
        
    return {
        "absolute": absolute,
        "useful": useful,
        "targets": targets,
    }

def rounder(num):
    if not num:
        return ""
    v = f"{num:,}" if type(num) == int else f"{num:,.1f}"
    return v.replace(",", " ")

def get_avgs(hits: list):
    if not hits:
        return []
    hits = sorted(hits)
    _len = len(hits)
    len10 = _len//10 or 1
    len50 = _len//2 or 1
    avgs = [
        sum(hits) // _len,
        max(hits),
        sum(hits[-len10:]) // len10,
        sum(hits[-len50:]) // len50,
        sum(hits[:len50]) // len50,
        sum(hits[:len10]) // len10,
        min(hits),
    ]
    return list(map(rounder, avgs))

def format_percent(hit, crit):
    if crit == 0:
        return ""
    percent = crit/(hit+crit)*100
    percent = rounder(percent)
    return f"{percent}%"

def format_hits_data(hits, crits):
    hits_count = len(hits)
    crits_count = len(crits)
    percent = format_percent(hits_count, crits_count)
    hits_avg = get_avgs(hits)
    crits_avg = get_avgs(crits)
    hits_count = rounder(hits_count)
    crits_count = rounder(crits_count)
    return ((hits_count, hits_avg), (crits_count, crits_avg)), percent

def format_hits(hits: dict[str, list[int]]):
    s_h, s_c, d_h, d_c = [hits.get(x, []) for x in HIT_TYPE]
    return format_hits_data(s_h, s_c), format_hits_data(d_h, d_c)

def hits_data(data: dict[int, dict[str, list[int]]]):
    return {spell_id: format_hits(hits) for spell_id, hits in data.items()}

def __test():
    import _main
    name = "22-05-04--21-05--Safiyah"
    report = _main.THE_LOGS(name)
    logs = report.get_logs()
    player_guid = '0x06000000004B2086'
    filter_guids = report.get_pets_of(player_guid)
    filter_guids.add(player_guid)
    parse_logs(logs, filter_guids, player_guid)
    parse_logs(logs, filter_guids, player_guid)
    parse_logs(logs, filter_guids, player_guid)
    parse_logs(logs, filter_guids, player_guid)
    parse_logs(logs, filter_guids, player_guid)

if __name__ == "__main__":
    __test()
