import json
import os
from collections import defaultdict

import logs_spells_order
import file_functions
import logs_auras
import logs_check_difficulty
import logs_deaths
import logs_dmg_breakdown
import logs_dmg_heals
import logs_dmg_useful
import logs_dps
import logs_fight_separator
import logs_get_time
import logs_player_spec
import logs_power
import logs_spell_info
import logs_spells_list
import logs_units_guid
import logs_valk_grabs
from constants import (
    BOSSES_FROM_HTML, CLASSES, FLAG_ORDER, LOGGER_REPORTS, LOGGER_UNUSUAL_SPELLS, LOGS_DIR, MONTHS,
    convert_to_html_name, duration_to_string, get_now, get_report_name_info, is_player, running_time,
    separate_thousands, setup_logger, sort_dict_by_value, to_dt_year_precise
)

IGNORED_ADDS = ['Treant', 'Shadowfiend', 'Ghouls']
PLAYER = "0x0"
SHIFT = {
    'spell': 10,
    'consumables': 10,
    'player_auras': 10,
}
_SORT = {"0xF": 1, "0x0": 2}

UNKNOWN_ICON = "inv_misc_questionmark"
DEFAULT_ICONS = [
    UNKNOWN_ICON,
    "ability_rogue_deviouspoisons",
    "ability_hunter_readiness",
    "ability_druid_catform",
]
_ICONS = [
    list(specs.values())
    for specs in CLASSES.values()
]
_ICONS.insert(0, DEFAULT_ICONS)
SPEC_ICON_TO_POSITION = {
    icon: (class_i, spec_i)
    for class_i, specs in enumerate(_ICONS)
    for spec_i, icon in enumerate(specs)
}

def get_shift(request_path: str):
    url_comp = request_path.split('/')
    try:
        return SHIFT.get(url_comp[3], 0)
    except IndexError:
        return 0

def add_new_numeric_data(data_total: defaultdict, data_new: dict[str, int]):
    for source, amount in data_new.items():
        data_total[source] += amount

def format_total_data(data: dict):
    data["Total"] = sum(data.values())
    return {k: separate_thousands(v) for k, v in data.items()}

def calc_percent(value: int, max_value: int):
    return int(value / max_value * 100)

def calc_per_sec(value: int, duration: float, precision: int=1):
    v = value / (duration or 1)
    precision = 10**precision
    v = int(v * precision) / precision
    return separate_thousands(v)

def calc_per_sec(value: int, duration: float, precision: int=1):
    v = value / (duration or 1)
    precision = 10**precision
    return int(v * precision) / precision

def convert_to_table(data: dict[str, int], duration):
    if not data:
        return ["- Total", "0", "0", "100"]
    _data = list(data.items())
    max_value = _data[0][1]
    total = sum(data.values())
    return [("- Total", separate_thousands(total), calc_per_sec(total, duration), "100")] + [
        (
            name,
            separate_thousands(value),
            calc_per_sec(value, duration),
            calc_percent(value, max_value),
        )
        for name, value in _data
    ]

TABLE_VALUES: dict[str, tuple[str]] = {
    "damage": ("damage", "dps", "d_p"),
    "heal": ("heal", "hps", "h_p"),
    "taken": ("taken", "tps", "t_p"),
}

def add_new_data(data: dict, table: dict[str, dict], duration: float, _type: str):
    KEYS = TABLE_VALUES[_type]
    if not data:
        return {KEYS[-1]: 0}
    
    TOTAL = {KEYS[-1]: 100}
    MAX_VALUE = max(data.values())
    for name, value in data.items():
        UNIT_DATA = table.setdefault(name, {})

        NEW_DATA = (
            value,
            calc_per_sec(value, duration),
            calc_percent(value, MAX_VALUE)
        )
        
        for key, new_value in zip(KEYS, NEW_DATA):
            if not key.endswith("_p"):
                UNIT_DATA[key] = separate_thousands(new_value)
                TOTAL[key] = TOTAL.get(key, 0) + new_value
            else:
                UNIT_DATA[key] = new_value

    return TOTAL

def count_total(spell_data: dict[str, dict[str, list[int]]]):
    return {
        spell_id: sum(sum(x) for x in d.values())
        for spell_id, d in spell_data.items()
    }

def count_total(spell_data: dict[str, dict[str, list[int]]]):
    new = {
        spell_id: sum(sum(x) for x in d.values())
        for spell_id, d in spell_data.items()
    }
    total = sum(new.values())
    new["Total"] = total
    total = total or 1
    return {
        spell_id: (separate_thousands(value), f"{separate_thousands(value / total * 100)}%")
        for spell_id, value in new.items()
    }

def format_totals(data: dict):
    return {k:separate_thousands(v) for k,v in data.items()}

def format_raw(raw_total: dict[int, int]):
    total = sum(raw_total.values())
    return {
        spell_id: (separate_thousands(value), separate_thousands(value / total * 100))+"%"
        for spell_id, value in raw_total.items()
    }

def build_query(boss_name_html, mode, s, f, attempt):
    slice_q = f"s={s}&f={f}" if s and f else ""
    
    if boss_name_html:
        query = f"boss={boss_name_html}"
        if mode:
            query = f"{query}&mode={mode}"
        query = f"{query}&{slice_q}"
        if attempt is not None:
            query = f"{query}&attempt={attempt}"
    else:
        query = slice_q
    
    if query:
        return f"?{query}"
    return ""

def group_targets(targets: set[str]):
    target_ids = {guid[:-6] for guid in targets}
    return {
        target_id: {guid for guid in targets if target_id in guid}
        for target_id in target_ids
    }

def regroup_targets(targets):
    grouped_targets = group_targets(targets)
    targets_players = set()
    
    for target_id in grouped_targets:
        if target_id[:3] == PLAYER:
            targets_players = grouped_targets.pop(target_id, set())
            break
    
    return set(grouped_targets) | targets_players

def sort_by_name_type(targets):
    targets = sorted(targets)
    targets = sorted(targets, key=lambda x: x[0][:5])
    targets = sorted(targets, key=lambda x: x[1])
    targets = sorted(targets, key=lambda x: _SORT[x[0][:3]])
    return dict(targets)

def get_dict_int(d: dict, key, default=0):
    try:
        v = d[key]
        try:
            return int(v)
        except Exception:
            return v
    except KeyError:
        return default


class THE_LOGS:
    def __init__(self, logs_name: str) -> None:
        self.loading = False
        self.NAME = logs_name
        self.PATH = os.path.join(LOGS_DIR, logs_name)
        if not os.path.exists(self.PATH):
            os.makedirs(self.PATH, exist_ok=True)
            LOGGER_REPORTS.debug(f"Created folder: {self.PATH}")

        self.year = int(logs_name[:2]) + 2000

        self.last_access = get_now()

        self.DURATIONS: dict[str, float] = {}
        self.TARGETS: dict[str, dict[str, set[str]]] = {}
        self.CACHE: dict[str, dict[str, dict]] = {x: {} for x in dir(self) if "__" not in x}
        self.CONTROLLED_UNITS: dict[str, set[str]] = {}

    def relative_path(self, s: str):
        return os.path.join(self.PATH, s)

    def get_formatted_name(self):
        try:
            return self.FORMATTED_NAME
        except AttributeError:
            report_name_info = get_report_name_info(self.NAME)
            time = report_name_info['time'].replace('-', ':')
            year, month, day = report_name_info['date'].split("-")
            month = MONTHS[int(month)-1][:3]
            date = f"{day} {month} {year}"
            name = report_name_info['name']
            self.FORMATTED_NAME = f"{date}, {time} - {name}"
            return self.FORMATTED_NAME

    def get_logger(self):
        try:
            return self.LOGGER_LOGS
        except AttributeError:
            log_file = self.relative_path('log.log')
            logger = setup_logger(f"{self.NAME}_logger", log_file)
            self.LOGGER_LOGS = logger
            return logger
    
    def cache_files_missing(self, files):
        try:
            return not os.path.isfile(files)
        except TypeError:
            return not all(os.path.isfile(file) for file in files)
    
    def get_fight_targets(self, s, f):
        return self.TARGETS[f"{s}_{f}"]
    
    def get_logs(self, s=None, f=None):
        try:
            logs = self.LOGS
        except AttributeError:
            logs_cut_file_name = self.relative_path("LOGS_CUT")
            logs = file_functions.zlib_text_read(logs_cut_file_name).splitlines()
            self.LOGS = logs
        
        return logs[s:f]

    def get_timedelta(self, last, now):
        return to_dt_year_precise(now, self.year) - to_dt_year_precise(last, self.year)
    
    def get_slice_first_last_lines(self, s, f):
        _slice = self.get_logs(s, f)
        return _slice[0], _slice[-1]
    
    def get_slice_duration(self, s, f):
        slice_ID = f"{s}_{f}"
        if slice_ID in self.DURATIONS:
            return self.DURATIONS[slice_ID]
        first_line, last_line = self.get_slice_first_last_lines(s, f)
        dur = self.get_timedelta(first_line, last_line).total_seconds()
        self.DURATIONS[slice_ID] = dur
        return dur

    def get_fight_duration_total(self, segments):
        return sum(self.get_slice_duration(s, f) for s, f in segments)

    def get_enc_data(self, rewrite=False):
        try:
            return self.ENCOUNTER_DATA
        except AttributeError:
            enc_data_file_name = self.relative_path("ENCOUNTER_DATA.json")
            if rewrite or self.cache_files_missing(enc_data_file_name):
                logs = self.get_logs()
                self.ENCOUNTER_DATA = logs_fight_separator.main(logs)
                file_functions.json_write(enc_data_file_name, self.ENCOUNTER_DATA, indent=None)
            else:
                enc_data: dict[str, list[tuple[int, int]]]
                enc_data = file_functions.json_read(enc_data_file_name)
                self.ENCOUNTER_DATA = enc_data
            return self.ENCOUNTER_DATA
    
    def new_guids(self):
        logs = self.get_logs()
        enc_data = self.get_enc_data()
        parsed = logs_units_guid.guids_main(logs, enc_data)

        if parsed['missing_owner']:
            LOGGER_REPORTS.error(f"{self.NAME} | Missing owners: {parsed['missing_owner']}")
        
        guids_data_file_name = self.relative_path("GUIDS_DATA.json")
        players_data_file_name = self.relative_path("PLAYERS_DATA.json")
        classes_data_file_name = self.relative_path("CLASSES_DATA.json")

        _guids = parsed['everything']
        _players = parsed['players']
        _classes = parsed['classes']
        
        file_functions.json_write(guids_data_file_name, _guids)
        file_functions.json_write(players_data_file_name, _players)
        file_functions.json_write(classes_data_file_name, _classes)
        
        return _guids, _players, _classes
    
    def get_guids(self, rewrite=False):
        try:
            return self.GUIDS, self.PLAYERS, self.CLASSES
        except AttributeError:
            _guids: dict[str, dict[str, str]]
            _players: dict[str, str]
            _classes: dict[str, str]

            files = [
                self.relative_path("GUIDS_DATA.json"),
                self.relative_path("PLAYERS_DATA.json"),
                self.relative_path("CLASSES_DATA.json")
            ]
            
            if rewrite or self.cache_files_missing(files):
                _guids, _players, _classes = self.new_guids()
            else:
                _guids, _players, _classes = [
                    file_functions.json_read_no_exception(_file_name)
                    for _file_name in files
                ]
            
            self.GUIDS, self.PLAYERS, self.CLASSES = _guids, _players, _classes
            return self.GUIDS, self.PLAYERS, self.CLASSES

    def get_all_guids(self):
        return self.get_guids()[0]

    def get_players_guids(self, whitelist_guids=None, whitelist_names=None):
        players = self.get_guids()[1]
        if whitelist_guids is not None:
            return {k:v for k,v in players.items() if k in whitelist_guids}
        elif whitelist_names is not None:
            return {k:v for k,v in players.items() if v in whitelist_names}
        else:
            return players

    def get_classes(self):
        return self.get_guids()[2]

    def guid_to_player_name(self):
        try:
            return self.PLAYERS_NAMES
        except AttributeError:
            players = self.get_players_guids()
            self.PLAYERS_NAMES = {v:k for k,v in players.items()}
            return self.PLAYERS_NAMES

    def name_to_guid(self, name: str) -> str:
        guids = self.get_all_guids()
        players_names = self.guid_to_player_name()

        if name in players_names:
            return players_names[name]
        for guid, data in guids.items():
            if data['name'] == name:
                return guid
    
    def guid_to_name(self, guid: str) -> str:
        guids = self.get_all_guids()
        players = self.get_players_guids()
        try:
            if guid in players:
                return players[guid]
            return guids[guid]["name"]
        except KeyError:
            for full_guid, p in guids.items():
                if guid in full_guid:
                    return p['name']
        
    def convert_data_guids_to_names(self, data: dict[str]):
        return {
            self.guid_to_name(guid): value
            for guid, value in data.items()
        }
    
        
    def get_master_guid(self, guid: str):
        guids = self.get_all_guids()
        master_guid = guids[guid].get('master_guid')
        if not master_guid:
            return guid
        return guids.get(master_guid, {}).get('master_guid', master_guid)

    def get_units_controlled_by(self, master_guid: str):
        try:
            int(master_guid, 16)
        except ValueError:
            master_guid = self.name_to_guid(master_guid)
        
        if master_guid in self.CONTROLLED_UNITS:
            return self.CONTROLLED_UNITS[master_guid]
        
        all_guids = self.get_all_guids()
        controlled_units = {
            guid
            for guid, p in all_guids.items()
            if p.get("master_guid") == master_guid
        }
        controlled_units.add(master_guid)
        self.CONTROLLED_UNITS[master_guid] = controlled_units
        return controlled_units

    def get_all_players_pets(self):
        try:
            return self.ALL_PETS
        except AttributeError:
            guids = self.get_all_guids()
            self.ALL_PETS = {
                guid
                for guid, p in guids.items()
                if p.get("master_guid", "").startswith(PLAYER)
            }
            return self.ALL_PETS

    def get_players_and_pets_guids(self):
        try:
            return self.PLAYERS_AND_PETS
        except AttributeError:
            players = set(self.get_players_guids())
            pets = self.get_all_players_pets()
            self.PLAYERS_AND_PETS = players | pets
            return self.PLAYERS_AND_PETS
            
    def get_classes_with_names(self):
        try:
            return self.CLASSES_NAMES
        except AttributeError:
            classes = self.get_classes()
            _classes_names: dict[str, str] = self.convert_data_guids_to_names(classes)
            self.CLASSES_NAMES = _classes_names
            return self.CLASSES_NAMES
        
    
    def get_timestamp(self, rewrite=False):
        try:
            return self.TIMESTAMP
        except AttributeError:
            timestamp_data_file_name = self.relative_path("TIMESTAMP_DATA.json")
            if rewrite or self.cache_files_missing(timestamp_data_file_name):
                logs = self.get_logs()
                self.TIMESTAMP = logs_get_time.get_timestamps(logs)
                file_functions.json_write(timestamp_data_file_name, self.TIMESTAMP, indent=None, sep=(",", ""))
            else:
                timestamp_data: list[int]
                timestamp_data = file_functions.json_read(timestamp_data_file_name)
                self.TIMESTAMP = timestamp_data
            return self.TIMESTAMP
    
    def find_index(self, n, shift=0):
        if n is None:
            return
        ts = self.get_timestamp()
        for i, line_n in enumerate(ts, -shift):
            if n <= line_n:
                return max(i, 0)

    def attempt_time(self, boss_name, attempt, shift=0):
        enc_data = self.get_enc_data()
        s, f = enc_data[boss_name][attempt]
        s = self.find_index(s, 2+shift)
        f = self.find_index(f, 1)
        return s, f
        
    def make_segment_query_segment(self, seg_info, boss_name, href2):
        attempt = seg_info['attempt']
        s, f = self.attempt_time(boss_name, attempt)
        href3 = f"{href2}&s={s}&f={f}&attempt={attempt}"
        class_name = f"{seg_info['attempt_type']}-link"
        segment_str = f"{seg_info['duration_str']} | {seg_info['segment_type']}"
        return {"href": href3, "class_name": class_name, "text": segment_str}
    
    def make_segment_query_diff(self ,segments, boss_name, href1, diff_id):
        href2 = f"{href1}&mode={diff_id}"
        a = {"href": href2, "class_name": "boss-link", "text": f"{diff_id} {boss_name}"}
        return {
            'link': a,
            'links': [
                self.make_segment_query_segment(seg_info, boss_name, href2)
                for seg_info in segments
            ]
        }

    def get_segment_queries(self):
        try:
            return self.SEGMENTS_QUERIES
        except AttributeError:
            segm_links: dict[str, dict] = {}
            separated = self.get_segments_separated()
            for boss_name, diffs in separated.items():
                href1 = f"?boss={convert_to_html_name(boss_name)}"
                a = {"href": href1, "class_name": "boss-link", "text": f"All {boss_name} segments"}
                segm_links[boss_name] = {
                    'link': a,
                    'links': {
                        diff_id: self.make_segment_query_diff(segments, boss_name, href1, diff_id)
                        for diff_id, segments in diffs.items()
                    }
                }
            self.SEGMENTS_QUERIES = segm_links
            return segm_links
        
    def get_segments_separated(self):
        try:
            return self.SEGMENTS_SEPARATED
        except AttributeError:
            segments = self.get_segments_data()
            self.SEGMENTS_SEPARATED = logs_check_difficulty.separate_modes(segments)
            return self.SEGMENTS_SEPARATED

    def get_segments_data(self):
        try:
            return self.SEGMENTS
        except AttributeError:
            logs = self.get_logs()
            enc_data = self.get_enc_data()
            self.SEGMENTS = logs_check_difficulty.get_segments(logs, enc_data)
            return self.SEGMENTS
    
    def segments_apply_shift(self, segments, shift_s=0, shift_f=0):
        if not shift_s and not shift_f:
            return
        
        ts = self.get_timestamp()
        for i, (seg_s, seg_f) in enumerate(segments):
            if shift_s:
                seg_s_shifted = self.find_index(seg_s, shift_s)
                seg_s = ts[seg_s_shifted]
            if shift_f:
                seg_f_shifted = self.find_index(seg_f, shift_f)
                seg_f = ts[seg_f_shifted]
            segments[i] = [seg_s, seg_f]
    
    def parse_request(self, path: str, args: dict) -> dict:
        segment_difficulty = args.get("mode")
        attempt = get_dict_int(args, "attempt")
        boss_name = BOSSES_FROM_HTML.get(args.get("boss"))
        ts = self.get_timestamp()
        sc = get_dict_int(args, "sc")
        fc = get_dict_int(args, "fc")
        if sc > 0 and fc < len(ts):
            slice_name = "Custom Slice"
            slice_tries = ""
            segments = [[ts[sc], ts[fc]]]
        elif not boss_name:
            slice_name = "Custom Slice"
            slice_tries = "All"
            s = get_dict_int(args, "s")
            f = get_dict_int(args, "f")
            if s and f:
                segments = [[ts[s], ts[f]]]
            else:
                segments =  [[None, None]]
        
        else:
            enc_data = self.get_enc_data()
            separated = self.get_segments_separated()
            slice_name = boss_name
            if attempt is not None:
                segments = [enc_data[boss_name][attempt], ]
                slice_tries = f"Try {attempt+1}"
                for diff, segm_data in separated[boss_name].items():
                    for segm in segm_data:
                        if segm.get("attempt") == attempt:
                            segment_type = segm.get("segment_type", "")
                            slice_tries = f"{diff} {segment_type}"
                            break
            elif segment_difficulty:
                slice_tries = f"{segment_difficulty} All"
                segments = [
                    [segment["start"], segment["end"]]
                    for segment in separated[boss_name][segment_difficulty]
                ]
            else:
                slice_tries = "All"
                segments = enc_data[boss_name]
            
            shift = get_shift(path)
            self.segments_apply_shift(segments, shift_s=shift)
        
        return {
            "SEGMENTS": segments,
            "SLICE_NAME": slice_name,
            "SLICE_TRIES": slice_tries,
            "BOSS_NAME": boss_name,
        }
    
    # def get_default_params(self, path: str, query: str, args: dict) -> dict:
    def get_default_params(self, request) -> dict:
        PATH: str = request.path
        QUERY: str = request.query_string.decode()
        if QUERY:
            QUERY = f"?{QUERY}"
        cached_data = self.CACHE['get_default_params'].setdefault(PATH, {})
        if QUERY in cached_data:
            return cached_data[QUERY]

        report_name_info = get_report_name_info(self.NAME)
        parsed = self.parse_request(PATH, request.args)
        duration = self.get_fight_duration_total(parsed["SEGMENTS"])
        return_data = parsed | {
            "PATH": PATH,
            "QUERY": QUERY,
            "REPORT_ID": self.NAME,
            "REPORT_NAME": self.get_formatted_name(),
            "SEGMENTS_LINKS": self.get_segment_queries(),
            "PLAYER_CLASSES": self.get_classes_with_names(),
            "DURATION": duration,
            "DURATION_STR": duration_to_string(duration),
            "SPEC_ICON_TO_POSITION": SPEC_ICON_TO_POSITION,
            "SERVER": report_name_info["server"],
        }
        cached_data[QUERY] = return_data
        return return_data


    def get_spells(self, rewrite=False):
        try:
            return self.SPELLS
        except AttributeError:
            spells_data_file_name = self.relative_path("SPELLS_DATA.json")
            if rewrite or self.cache_files_missing(spells_data_file_name):
                logs = self.get_logs()
                self.SPELLS = logs_spells_list.get_all_spells(logs)
                file_functions.json_write(spells_data_file_name, self.SPELLS)
            else:
                _spells = file_functions.json_read_no_exception(spells_data_file_name)
                self.SPELLS = logs_spells_list.spell_id_to_int(_spells)
            return self.SPELLS

    def get_spell_name(self, spell_id):
        _spells = self.get_spells()
        spell_id = abs(int(spell_id))
        if spell_id in _spells:
            return _spells[spell_id]["name"]
        return "Unknown spell"

    def get_spells_colors(self, spells) -> dict[int, str]:
        if not spells:
            return {}
        all_spells = self.get_spells()
        return {
            spell_id: all_spells[abs(spell_id)]['color']
            for spell_id in spells
            if abs(spell_id) in all_spells
        }

    def get_spells_lower(self):
        try:
            return self.SPELLS_LOWER
        except AttributeError:
            spells = self.get_spells()
            self.SPELLS_LOWER = {spell_id: v["name"].lower() for spell_id, v in spells.items()}
            return self.SPELLS_LOWER

    def get_spells_ids(self):
        try:
            return self.SPELLS_IDS
        except AttributeError:
            spells = self.get_spells()
            self.SPELLS_IDS = {spell_id: str(spell_id) for spell_id in spells}
            return self.SPELLS_IDS

    @running_time
    def filtered_spell_list(self, request: dict[str, str]):
        if 'filter' not in request:
            return {}
        
        INPUT = request['filter'].lower()
        SPELLS = self.get_spells()

        _spells = self.get_spells_ids() if INPUT.isdigit() else self.get_spells_lower()

        return {
            spell_id: SPELLS[spell_id]['name']
            for spell_id, spell_v in _spells.items()
            if INPUT in spell_v
        }


    @running_time
    def report_page(self, s, f) -> dict[str, defaultdict[str, int]]:
        slice_ID = f"{s}_{f}"
        cached_data = self.CACHE['report_page']
        if slice_ID in cached_data:
            return cached_data[slice_ID]
        
        logs_slice = self.get_logs(s, f)
        players_and_pets = self.get_players_and_pets_guids()
        data = logs_dmg_heals.parse_both(logs_slice, players_and_pets)
        
        data['specs'] = self.get_players_specs_in_segments(s, f)
        data['first_hit'] = logs_dmg_heals.readable_logs_line(logs_slice[0])
        data['last_hit'] = logs_dmg_heals.readable_logs_line(logs_slice[-1])

        cached_data[slice_ID] = data
        return data
    
    def dry_data(self, data, slice_duration):
        guids = self.get_all_guids()
        data_with_pets = logs_dmg_heals.add_pets(data, guids)
        data_sorted = sort_dict_by_value(data_with_pets)
        return convert_to_table(data_sorted, slice_duration)

    def report_add_spec_info(self, specs: dict[str, int], data: dict[str, dict]):
        classes_names = self.get_classes_with_names()

        new_specs: dict[str, tuple(str, str)] = {}
        for unit_name in data:
            if unit_name.endswith('-A'):
                new_specs[unit_name] = ('Mutated Abomination', 'ability_rogue_deviouspoisons')
            elif unit_name == "Total":
                new_specs[unit_name] = ('Total', 'ability_hunter_readiness')
            elif unit_name in classes_names:
                new_specs[unit_name] = logs_player_spec.get_spec_info(specs[unit_name])
        return new_specs

    def get_report_page_all(self, segments):
        DATA = {
            "damage": defaultdict(int),
            "heal": defaultdict(int),
            "taken": defaultdict(int),
        }
        SPECS = {}

        total = {}
        TABLE = {
            "Total": total
        }

        return_dict = {
            "TABLE": TABLE,
        }


        for s, f in segments:
            new_data = self.report_page(s, f)
            for k, _data in DATA.items():
                add_new_numeric_data(_data, new_data[k])

            SPECS |= new_data['specs']

            return_dict.setdefault("FIRST_HIT", new_data['first_hit'])
            return_dict["LAST_HIT"] = new_data['last_hit']

        total_duration = self.get_fight_duration_total(segments)

        GUIDS = self.get_all_guids()
        for k, _data in DATA.items():
            data_with_pets = logs_dmg_heals.add_pets(_data, GUIDS)
            if k == "damage":
                data_with_pets = sort_dict_by_value(data_with_pets)
            total |= add_new_data(data_with_pets, TABLE, total_duration, k)

        for k, v in total.items():
            total[k] = separate_thousands(v) 
        
        SPECS = self.convert_data_guids_to_names(SPECS)
        SPECS = self.report_add_spec_info(SPECS, TABLE)
        for name, (spec_name, spec_icon) in SPECS.items():
            TABLE[name]['spec_name'] = spec_name
            TABLE[name]['spec_icon'] = spec_icon
        
        return return_dict


    def player_damage(self, s, f, player_GUID, filter_GUID=None):
        slice_ID = f"{s}_{f}"
        cached_data = self.CACHE['player_damage'].setdefault(player_GUID, {}).setdefault(filter_GUID, {})
        if slice_ID in cached_data:
            return cached_data[slice_ID]

        logs_slice = self.get_logs(s, f)
        controlled_units = self.get_units_controlled_by(player_GUID)
        all_player_pets = self.get_players_and_pets_guids()
        data = logs_dmg_breakdown.parse_logs_wrap(logs_slice, player_GUID, controlled_units, all_player_pets, filter_GUID)
        cached_data[slice_ID] = data
        return data
    
    def player_damage_taken(self, s, f, player_GUID, filter_GUID=None):
        slice_ID = f"{s}_{f}"
        cached_data = self.CACHE['player_damage_taken'].setdefault(player_GUID, {}).setdefault(filter_GUID, {})
        if slice_ID in cached_data:
            return cached_data[slice_ID]

        logs_slice = self.get_logs(s, f)
        data = logs_dmg_breakdown.parse_logs_taken(logs_slice, player_GUID, source_filter=filter_GUID)
        cached_data[slice_ID] = data
        return data
    
    def player_heal(self, s, f, player_GUID, filter_GUID=None):
        slice_ID = f"{s}_{f}"
        cached_data = self.CACHE['player_heal'].setdefault(player_GUID, {}).setdefault(filter_GUID, {})
        if slice_ID in cached_data:
            return cached_data[slice_ID]

        logs_slice = self.get_logs(s, f)
        controlled_units = self.get_units_controlled_by(player_GUID)
        all_player_pets = self.get_players_and_pets_guids()
        data = logs_dmg_breakdown.parse_logs_heal(logs_slice, player_GUID, controlled_units, all_player_pets, filter_GUID)
        cached_data[slice_ID] = data
        return data

    def player_damage_gen(self, segments, player_GUID, filter_GUID=None):
        for s, f in segments:
            yield self.player_damage(s, f, player_GUID, filter_GUID)

    def player_damage_taken_gen(self, segments, player_GUID, filter_GUID=None):
        for s, f in segments:
            yield self.player_damage_taken(s, f, player_GUID, filter_GUID)

    def player_heal_gen(self, segments, player_GUID, filter_GUID=None):
        for s, f in segments:
            yield self.player_heal(s, f, player_GUID, filter_GUID)

    def player_damage_sum(self, data_gen):
        dmg_data = defaultdict(lambda: defaultdict(int))
        units: set[str] = set()
        dmg_data["units"] = units
        actual = defaultdict(lambda: defaultdict(list))
        dmg_data["actual"] = actual

        for data in data_gen:
            for k, v in data.items():
                if k == "units":
                    units.update(v)
                elif k == "actual":
                    for spell_id, cats in v.items():
                        spells = actual[spell_id]
                        for hit_type, hits in cats.items():
                            spells[hit_type].extend(hits)
                else:
                    add_new_numeric_data(dmg_data[k], v)

        return dmg_data
    
    @running_time
    def player_damage_format(self, _data):
        spell_data = self.get_spells()
        def spell_name(spell_id):
            try:
                if spell_id < 0:
                    return f"{spell_data[-spell_id]['name']} (Pet)"
                return spell_data[spell_id]['name']
            except KeyError:
                return spell_id
        
        targets_set = regroup_targets(_data["units"])
        targets = [
            (gid, self.guid_to_name(gid))
            for gid in targets_set
        ]
        targets = sort_by_name_type(targets)
        
        actual = _data["actual"]
        hits_data = logs_dmg_breakdown.hits_data(actual)
        actual_sum = {
            spell_id: sum(sum(x) for x in d.values())
            for spell_id, d in actual.items()
        }
        actual_sorted = sort_dict_by_value(actual_sum)
        spell_names = {spell_id: spell_name(spell_id) for spell_id in actual_sorted}
        spell_colors = self.get_spells_colors(spell_names)
        
        reduced = _data['reduced']
        reduced_formatted = format_total_data(reduced)
        reduced_percent = {
            spell_id: f"{((reduced[spell_id] / (value + reduced[spell_id])) * 100):.1f}%"
            for spell_id, value in actual_sum.items()
            if reduced.get(spell_id)
        }
        
        actual_formatted = format_total_data(actual_sum)
        actual_total = actual_sum['Total'] or 1
        actual_percent =  {
            spell_id: f"{(value / actual_total * 100):.1f}%"
            for spell_id, value in actual_sum.items()
        }
        
        if _data['casts']:
            dmg_hits = {spell_name(spell_id): value for spell_id, value in _data['dmg_hits'].items()}
            auras = {spell_name(spell_id): value for spell_id, value in _data['auras'].items()}
            casts = {spell_name(spell_id): value for spell_id, value in _data['casts'].items()}
            casts = dmg_hits | auras | casts
            misses = {spell_name(spell_id): value for spell_id, value in _data['misses'].items()}
        else:
            casts = {}
            misses = {}


        return {
            "TARGETS": targets,
            "NAMES": spell_names,
            "COLORS": spell_colors,
            "ACTUAL": actual_formatted,
            "ACTUAL_PERCENT": actual_percent,
            "REDUCED": reduced_formatted,
            "REDUCED_PERCENT": reduced_percent,
            "HITS": hits_data,
            "CASTS": casts,
            "MISSES": misses,
        }
    
    
    def get_comp_data(self, segments, class_filter: str, tGUID=None):
        class_filter = class_filter.lower()
        response = []
        for guid, class_name in self.get_classes().items():
            if class_name != class_filter:
                continue
            name = self.guid_to_name(guid)
            data_gen = self.player_damage_gen(segments, guid, tGUID)
            data_sum = self.player_damage_sum(data_gen)
            data = {
                "name": name,
                "data": self.player_damage_format(data_sum)
            }
            response.append(data)
        return json.dumps(response)

    
    # POTIONS

    def potions_info(self, s, f) -> dict[str, dict[str, int]]:
        slice_ID = f"{s}_{f}"
        cached_data = self.CACHE['potions_info']
        if slice_ID in cached_data:
            return cached_data[slice_ID]

        logs_slice = self.get_logs(s, f)
        data = logs_spell_info.get_potions_count(logs_slice)
        cached_data[slice_ID] = data
        return data
    
    def convert_dict_guids_to_name(self, data: dict):
        return {self.guid_to_name(guid): v for guid, v in data.items()}

    def add_missing_players(self, data, default=0, players=None):
        if players is None:
            players = self.get_players_guids()
        for guid in players:
            if guid not in data:
                data[guid] = default
        return data
    
    def potions_all(self, segments):
        potions = defaultdict(lambda: defaultdict(int))
        players = set()

        for s, f in segments:
            _potions = self.potions_info(s, f)
            for spell_id, sources in _potions.items():
                add_new_numeric_data(potions[spell_id], sources)
                
            _report_page = self.report_page(s, f)
            players.update(_report_page["specs"])
        
        pots = {x: self.convert_dict_guids_to_name(y) for x,y in potions.items()}
        
        p_total = logs_spell_info.count_total(potions)
        p_total = self.convert_dict_guids_to_name(p_total)
        for name in players:
            if name not in p_total:
                p_total[name] = 0
        p_total = dict(sorted(p_total.items()))
        p_total = sort_dict_by_value(p_total)

        return {
            "ITEM_INFO": logs_spell_info.ITEM_INFO,
            "ITEMS_TOTAL": p_total,
            "ITEMS": pots,
        }

    def auras_info(self, s, f):
        data: defaultdict[str, dict[str, tuple[int, float]]]
        slice_ID = f"{s}_{f}"
        cached_data = self.CACHE['auras_info']
        if slice_ID in cached_data:
            data = cached_data[slice_ID]
            return data

        logs_slice = self.get_logs(s, f)
        data = logs_spell_info.get_raid_buff_count(logs_slice)
        data = logs_spell_info.get_auras_uptime(logs_slice, data)
        cached_data[slice_ID] = data
        return data

    def auras_info_all(self, segments, trim_non_players=True):
        auras_uptime = defaultdict(lambda: defaultdict(list))
        auras_count = defaultdict(lambda: defaultdict(int))

        for s, f in segments:
            _auras = self.auras_info(s, f)
            for guid, aura_data in _auras.items():
                if trim_non_players and not is_player(guid):
                    continue
                for spell_id, (count, uptime) in aura_data.items():
                    auras_count[guid][spell_id] += count
                    auras_uptime[guid][spell_id].append(uptime)

        aura_info_set = set()
        auras_uptime_formatted = defaultdict(lambda: defaultdict(float))
        for guid, aura_data in auras_uptime.items():
            for spell_id, uptimes in aura_data.items():
                aura_info_set.add(spell_id)
                v = sum(uptimes) / len(uptimes) * 100
                auras_uptime_formatted[guid][spell_id] = f"{v:.2f}"
        
        self.add_missing_players(auras_count, {})
        self.add_missing_players(auras_uptime, {})

        auras_count_with_names = self.convert_dict_guids_to_name(auras_count)
        auras_uptime_with_names = self.convert_dict_guids_to_name(auras_uptime_formatted)

        filtered_aura_info = logs_spell_info.get_filtered_info(aura_info_set)

        return {
            "AURA_UPTIME": auras_uptime_with_names,
            "AURA_COUNT": auras_count_with_names,
            "AURA_INFO": filtered_aura_info,
        }


    @running_time
    def get_spell_count(self, s, f, spell_id_str) -> dict[str, dict[str, int]]:
        slice_ID = f"{s}_{f}"
        cached_data = self.CACHE['get_spell_count'].setdefault(spell_id_str, {})
        if slice_ID in cached_data:
            return cached_data[slice_ID]
            
        logs_slice = self.get_logs(s, f)
        spells = logs_spell_info.get_spell_count(logs_slice, spell_id_str)
        cached_data[slice_ID] = spells
        return spells
    
    def spell_count_all(self, segments, spell_id: str):
        spell_id = spell_id.replace("-", "")
        all_spells = self.get_spells()
        if int(spell_id) not in all_spells:
            LOGGER_REPORTS.error(f"{spell_id} not in spells")
            return {
                "SPELLS": {},
                "TABS": {},
            }
        
        spells: dict[str, dict[str, dict[str, int]]] = {}

        for s, f in segments:
            _spells = self.get_spell_count(s, f, spell_id)
            for flag, _types in _spells.items():
                _flag = spells.setdefault(flag, {})
                for _type, names in _types.items():
                    _t = _flag.setdefault(_type, {})
                    for name, value in names.items():
                        _t[name] = _t.get(name, 0) + value
        
        spells = {x: spells[x] for x in FLAG_ORDER if x in spells}

        for flag_info in spells.values():
            for sources, sources_info in flag_info.items():
                flag_info[sources] = sort_dict_by_value(sources_info)
        
        tabs = [(flag.lower().replace('_', '-'), flag) for flag in spells]

        _spells = self.get_spells()
        s_id = abs(int(spell_id))
        spell_name = _spells.get(s_id, {}).get('name', '')
        spell_name = f"{spell_id} {spell_name}"

        return {
            "SPELLS": spells,
            "TABS": tabs,
            "SPELL_NAME": spell_name,
            "SPELL_ID": s_id,
        }


    def useful_damage(self, s, f, targets, boss_name):
        slice_ID = f"{s}_{f}"
        cached_data = self.CACHE['useful_damage']
        if slice_ID in cached_data:
            return cached_data[slice_ID]

        logs_slice = self.get_logs(s, f)

        useful = logs_dmg_useful.specific_useful(logs_slice, boss_name)
        damage = logs_dmg_useful.get_dmg(logs_slice, targets)
        data = {
            "damage": damage,
            "useful": useful,
        }
        cached_data[slice_ID] = data
        return data

    def convert_data_to_names(self, data: dict):
        guids = self.get_all_guids()
        return {
            guids[guid]["name"]: v
            for guid, v in data.items()
            if guid in guids
        }
    
    def add_total_and_names(self, data: dict):
        data_names = self.convert_data_to_names(data)
        data_names["Total"] = sum(data.values())
        return sort_dict_by_value(data_names)
    
    def data_visual_format(self, data):
        data_names = self.add_total_and_names(data)
        return {
            name: separate_thousands(v)
            for name, v in data_names.items()
        }

    @running_time
    def useful_damage_all(self, segments, boss_name):
        all_data = defaultdict(lambda: defaultdict(int))
        all_data_useful = defaultdict(lambda: defaultdict(int))

        boss_guid_id = self.name_to_guid(boss_name)
        targets = logs_dmg_useful.get_all_targets(boss_name, boss_guid_id)
        targets_useful = targets["useful"]
        targets_all = targets["all"]
        table_heads = []

        for s, f in segments:
            data = self.useful_damage(s, f, targets_all, boss_name)
            for target_name in data["useful"]:
                targets_useful[target_name] = target_name
            
            _damage: dict[str, dict[str, int]] = data["damage"]
            for guid_id, _dmg_new in _damage.items():
                add_new_numeric_data(all_data[guid_id], _dmg_new)
            
            _damage: dict[str, dict[str, int]] = data["damage"] | data["useful"]
            for guid_id, _dmg_new in _damage.items():
                add_new_numeric_data(all_data_useful[guid_id], _dmg_new)

        guids = self.get_all_guids()
        all_data = logs_dmg_useful.combine_pets_all(all_data, guids, trim_non_players=True)
        all_data_useful = logs_dmg_useful.combine_pets_all(all_data_useful, guids, trim_non_players=True)

        dmg_total = logs_dmg_useful.get_total_damage(all_data)
        dmg_useful = logs_dmg_useful.get_total_damage(all_data_useful, targets_useful)

        players = dmg_total | dmg_useful
        players = self.add_total_and_names(players)

        dmg_useful = self.data_visual_format(dmg_useful)
        table_heads.append("Total Useful")
        
        dmg_total = self.data_visual_format(dmg_total)
        table_heads.append("Total")
    
        custom_units = logs_dmg_useful.add_custom_units(all_data, boss_name)
        all_data = custom_units | all_data

        dmg_to_target = {}
        ____data = logs_dmg_useful.guid_to_useful_name(all_data_useful) | all_data
        for guid_id, _data in ____data.items():
            if not _data:
                continue
            table_heads.append(targets_all.get(guid_id, guid_id))
            dmg_to_target[guid_id] = self.data_visual_format(_data)

        return {
            "HEADS": table_heads,
            "TOTAL": dmg_total,
            "TOTAL_USEFUL": dmg_useful,
            "TARGETS": dmg_to_target,
            "PLAYERS": players,
        }

    def sort_spell_data_by_name(self, data: dict):
        spells = self.get_spells()
        return dict(sorted(data.items(), key=lambda x: spells[x[0]]["name"]))

    def get_auras(self, s, f, filter_guid):
        logs_slice = self.get_logs(s, f)
        a = logs_auras.AurasMain(logs_slice)
        data = a.main(filter_guid)
        # buffs = self.sort_spell_data_by_name(data["buffs"])
        # debuffs = self.sort_spell_data_by_name(data["debuffs"])
        spell_colors = self.get_spells_colors(data['spells'])
        all_spells = self.get_spells()
        return {
            'BUFFS': data["buffs"],
            'DEBUFFS': data["debuffs"],
            # 'BUFFS': buffs,
            # 'DEBUFFS': debuffs,
            'COLORS': spell_colors,
            'ALL_SPELLS': all_spells,
            "BUFF_UPTIME": data['buffs_uptime'],
            "DEBUFF_UPTIME": data['debuffs_uptime'],
        }

    def get_auras_all(self, segments, player_name):
        durations = []

        filter_guid = self.name_to_guid(player_name)

        for s, f in segments:
            data = self.get_auras(s, f, filter_guid)
            buffs = data['buffs']
            durations.append(self.get_slice_duration(s, f))
    


    def logs_custom_search(self, query: dict[str, str]):
        logs = self.get_logs()
        # for 
        return 'Spell not found'


    def pretty_print_players_data(self, data):
        guids = self.get_all_guids()
        data = sort_dict_by_value(data)
        for guid, value in data.items():
            print(f"{guids[guid]['name']:<12} {separate_thousands(value):>13}")

    def get_players_specs_in_segments(self, s, f):
        logs_slice = self.get_logs(s, f)
        players = self.get_players_guids()
        classes = self.get_classes()
        return logs_player_spec.get_specs_no_names(logs_slice, players, classes)


    def grabs_info(self, s, f):
        slice_ID = f"{s}_{f}"
        cached_data = self.CACHE['grabs_info']
        if slice_ID in cached_data:
            return cached_data[slice_ID]
        
        logs_slice = self.get_logs(s, f)
        players = self.get_players_guids()
        grabs = logs_valk_grabs.main(logs_slice, players)
        cached_data[slice_ID] = grabs
        return grabs

    def valk_info_all(self, segments):
        grabs_total = defaultdict(int)
        all_grabs = []
        for s, f in segments:
            grabs = self.grabs_info(s, f)
            if grabs is None:
                continue
            all_grabs.extend(grabs)
            for g in grabs:
                for p in g:
                    grabs_total[p] += 1
        waves = list(range(1, len(all_grabs)+1))
        grabs_total = dict(sorted(grabs_total.items()))
        grabs_total = sort_dict_by_value(grabs_total)
        return {
            "ALL_GRABS": all_grabs,
            "GRABS_TOTAL": grabs_total,
            "WAVES": waves,
        }


    def dmg_taken(self, logs_slice, filter_guids=None, players=False):
        if filter_guids is None:
            filter_guid = PLAYER if players else '0xF1'
            dmg = logs_dmg_heals.parse_dmg_taken_single(logs_slice, filter_guid)
        else:
            dmg = logs_dmg_heals.parse_dmg_taken(logs_slice, filter_guids)
        new_data: dict[str, dict[str, int]] = {}
        for tguid, sources in dmg.items():
            name = self.guid_to_name(tguid)
            q = new_data.setdefault(name, {})
            for sguid, value in sources.items():
                sguid = self.get_master_guid(sguid)
                q[sguid] = q.get(sguid, 0) + value
        b = next(iter(new_data))
        new_data[b] = sort_dict_by_value(new_data[b])
        for name in IGNORED_ADDS:
            new_data.pop(name, None)
        for d in new_data.values():
            d.pop('nil', None)
        players = self.get_players_guids()
        return {
            target_name: {players[guid]: separate_thousands(value) for guid, value in sources.items() if guid in players}
            for target_name, sources in new_data.items()
        }


    def death_info(self, s, f, guid):
        slice_ID = f"{s}_{f}"
        cached_data = self.CACHE['death_info'].setdefault(guid, {})
        if slice_ID in cached_data:
            return cached_data[slice_ID]
        
        logs_slice = self.get_logs(s, f)
        deaths = logs_deaths.get_deaths(logs_slice, guid)
        logs_deaths.sfjsiojfasiojfiod(deaths)
        cached_data[slice_ID] = deaths
        return deaths
    
    def get_deaths(self, segments, guid):
        deaths = {}
        if guid:
            for s, f in segments:
                deaths |= self.death_info(s, f, guid)
        return {
            "DEATHS": deaths,
            "CLASSES": self.get_classes(),
            "PLAYERS": self.get_players_guids(),
            "GUIDS": self.get_all_guids(),
            "SPELLS": self.get_spells(),
        }


    def get_powers(self, s, f):
        slice_ID = f"{s}_{f}"
        cached_data = self.CACHE['get_powers']
        if slice_ID in cached_data:
            return cached_data[slice_ID]
        
        logs_slice = self.get_logs(s, f)
        data = logs_power.asidjioasjdso(logs_slice)
        cached_data[slice_ID] = data
        return data

    def powers_add_data(
        self,
        data: dict[str, dict[str, dict[str, int]]],
        new_data: dict[str, dict[str, dict[str, int]]]
    ):
        for power_name, targets in new_data.items():
            for guid, spells in targets.items():
                name = self.guid_to_name(guid)
                _guid = self.get_master_guid(guid)
                if _guid != guid:
                    master_name = self.guid_to_name(_guid)
                    name = f"{name} ({master_name})"
                
                for spell_id, value in spells.items():
                    data[power_name][name][spell_id] += value
    
    def get_power_data(self, spell_data, spell_id):
        if spell_id in spell_data:
            return spell_data[spell_id]

        spell_info = dict(logs_power.SPELLS.get(spell_id, {}))
        if not spell_info:
            spell_info = {
                "icon": UNKNOWN_ICON,
                "name": self.get_spell_name(spell_id)
            }
            LOGGER_UNUSUAL_SPELLS.info(f"{self.NAME} {spell_id} missing info")
        
        spell_info["value"] = 0
        spell_data[spell_id] = spell_info
        return spell_info

    @running_time
    def get_powers_all(self, segments):
        SPELLS: dict[str, dict] = {}
        TOTAL = defaultdict(lambda: defaultdict(int))
        POWERS = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))

        for s, f in segments:
            _data = self.get_powers(s, f)
            self.powers_add_data(POWERS, _data)
        
        for power_name, targets in POWERS.items():
            spell_data = {}
            for target_name, target_spells in targets.items():
                for spell_id, value in target_spells.items():
                    target_spells[spell_id] = separate_thousands(value)

                    TOTAL[power_name][target_name] += value
                    
                    spell_info = self.get_power_data(spell_data, spell_id)
                    spell_info["value"] += value
            
            SPELLS[power_name] = dict(sorted(spell_data.items(), key=lambda x: x[1]["value"], reverse=True))
            for power_data in spell_data.values():
                power_data["value"] = separate_thousands(power_data["value"])
        
        for targets in TOTAL.values():
            for target_name, value in targets.items():
                targets[target_name] = separate_thousands(value)

        labels = [(i, p) for i, p in enumerate(logs_power.POWER_TYPES.values()) if p in POWERS]
        
        return {
            "POWERS": POWERS,
            "TOTAL": TOTAL,
            "SPELLS": SPELLS,
            "LABELS": labels,
        }

    def get_dps(self, s, f, player: str):
        slice_ID = f"{s}_{f}"
        if player:
            slice_ID = f"{slice_ID}_{player}"
        cached_data = self.CACHE['get_dps']
        if slice_ID in cached_data:
            return cached_data[slice_ID]

        logs_slice = self.get_logs(s, f)
        if player:
            guids = self.get_units_controlled_by(player)
        else:
            guids = self.get_players_and_pets_guids()
        data = logs_dps.get_raw_data(logs_slice, guids)
        logs_dps.convert_keys(data)

        cached_data[slice_ID] = data
        return data

    def get_dps_wrap(self, data: dict):
        if not data:
            return {}

        enc_name = data.get("boss")
        attempt = data.get("attempt")
        if not enc_name or not attempt:
            return {}
        
        enc_data = self.get_enc_data()
        enc_name = BOSSES_FROM_HTML[enc_name]
        s, f = enc_data[enc_name][int(attempt)]
        player = data.get("player_name")
        _data = self.get_dps(s, f, player)
        refresh_window = data.get("sec")
        new_data = logs_dps.convert_to_dps(_data, refresh_window)
        logs_dps.convert_keys_to_str(new_data)
        return new_data

    @running_time
    def get_spell_history(self, s, f, guid) -> dict[str, defaultdict[str, int]]:
        slice_ID = f"{s}_{f}"
        cached_data = self.CACHE['get_spell_history'].setdefault(guid, {})
        if slice_ID in cached_data:
            return cached_data[slice_ID]
        
        logs_slice = self.get_logs(s, f)
        players_and_pets = self.get_players_and_pets_guids()
        data = logs_spells_order.get_history(logs_slice, guid, players_and_pets)
        _spells = self.get_spells()
        _flags = [flag for flag in FLAG_ORDER if flag in data["FLAGS"]]
        _other = sorted(set(data["FLAGS"]) - set(_flags))
        data["FLAGS"] = _flags + _other
        data["SPELLS"] = {
            x: _spells[int(x)]
            for x in data["DATA"]
        }

        cached_data[slice_ID] = data
        return data
    
    def get_spell_history_wrap(self, segments: dict, player_name: str):
        s, f = segments[0]
        player = self.name_to_guid(player_name)
        _data = self.get_spell_history(s, f, player)
        _spells = {}
        for spell_id in _data["SPELLS"]:
            try:
                _spells[spell_id] = logs_spells_order.SPELLS3[spell_id]
            except KeyError:
                _spells[spell_id] = UNKNOWN_ICON
        _data["SPELL_ICONS"] = _spells
        _data["RDURATION"] = self.get_slice_duration(s, f)
        return _data
    