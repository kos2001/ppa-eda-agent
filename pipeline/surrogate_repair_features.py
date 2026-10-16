"""Offline pre-run FanoutRepair features; never inspect generated topology.

Structural constraints follow flows/fanout_repair.py and
flows/fanout_buffer.py. Absent historical settings remain unknown; current
defaults cannot establish the behavior of an older flow snapshot. A valid encoding does not establish that named nets,
pins or masters exist, or that driver replacements are functionally valid.
"""
import hashlib
import json
import math
import re

import surrogate

SCHEMA_VERSION = 2
UNKNOWN_MAP = "<unknown:setting-absent>"
REPAIR_KEYS = {
    'FANOUT_REPAIR_LIMIT', 'FANOUT_REPAIR_ROUNDS',
    'FANOUT_REPAIR_SIGNAL_CELL', 'FANOUT_REPAIR_CLOCK_CELL',
    'FANOUT_REPAIR_NET_LIMITS', 'FANOUT_REPAIR_DIRECT_SINKS',
    'FANOUT_REPAIR_DRIVER_CELLS',
}


def _integer(value, key, minimum, maximum=None):
    if type(value) is not int or value < minimum or (maximum is not None and value > maximum):
        raise ValueError('invalid ' + key)
    return value


def _name(value):
    return isinstance(value, str) and bool(value.strip()) and value == value.strip()


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON key: ' + key)
        result[key] = value
    return result


def _json(value, key, expected=dict):
    if isinstance(value, str):
        try:
            value = json.loads(value, object_pairs_hook=_pairs)
        except (ValueError, TypeError) as error:
            raise ValueError('invalid ' + key + ': ' + str(error)) from error
    if not isinstance(value, expected):
        raise ValueError('invalid ' + key)
    return value


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def featurize(row):
    """Merge declared settings with overrides, returning numeric/categorical inputs.

    Missing flow is unknown, not assumed Classic. Unknown repair switches fail
    closed. The structural fingerprint is categorical, never a numeric hash.
    """
    flow = row.get('flow')
    if flow not in ('Classic', 'FanoutRepair'):
        raise ValueError('unsupported_flow_features:' + str(flow))
    declared, overrides = row.get('declared', {}), row.get('overrides', {})
    if not isinstance(declared, dict) or not isinstance(overrides, dict):
        raise ValueError('invalid declared/overrides')
    config = {**declared, **overrides}
    # Validate explicit inputs before the legacy featurizer can coerce booleans
    # or silently turn malformed settings into missing values.
    def finite_number(value):
        return type(value) in (int, float) and math.isfinite(value)

    for key in ('FP_CORE_UTIL', 'PL_TARGET_DENSITY_PCT', 'CLOCK_PERIOD'):
        if key in config and not finite_number(config[key]):
            raise ValueError('invalid ' + key)
    if 'DIE_AREA' in config:
        die = config['DIE_AREA']
        if (not isinstance(die, (list, tuple)) or len(die) != 4
                or not all(finite_number(value) for value in die)
                or die[2] <= die[0] or die[3] <= die[1]):
            raise ValueError('invalid DIE_AREA')
    if 'SYNTH_STRATEGY' in config and not _name(config['SYNTH_STRATEGY']):
        raise ValueError('invalid SYNTH_STRATEGY')
    for key in config:
        if not isinstance(key, str):
            raise ValueError('invalid setting name')
        if key.startswith('FANOUT_REPAIR_') and key not in REPAIR_KEYS:
            raise ValueError('unknown repair setting:' + key)
        if key.startswith('DIODE_TRIM_') and key not in ('DIODE_TRIM_KEEP', 'DIODE_TRIM_MIN_DIODES'):
            raise ValueError('unknown repair setting:' + key)
        if key.startswith('MACRO_BUFFER_'):
            if key != 'MACRO_BUFFER_PINS' or _json(config[key], key, list):
                raise ValueError('unsupported macro buffer setting:' + key)
    if flow == 'Classic' and any(key in REPAIR_KEYS or key.startswith('DIODE_TRIM_') for key in config):
        raise ValueError('repair settings on Classic flow')
    features = surrogate.featurize({**row, 'overrides': config})
    if flow == 'Classic':
        return features
    limit = _integer(config.get('FANOUT_REPAIR_LIMIT'), 'FANOUT_REPAIR_LIMIT', 2)
    rounds = (_integer(config['FANOUT_REPAIR_ROUNDS'], 'FANOUT_REPAIR_ROUNDS', 1, 3)
              if 'FANOUT_REPAIR_ROUNDS' in config else None)
    cells = {}
    for key in ('FANOUT_REPAIR_SIGNAL_CELL', 'FANOUT_REPAIR_CLOCK_CELL'):
        value = config.get(key)
        if not isinstance(value, str) or not re.fullmatch(r'sky130_fd_sc_hd__(?:clk)?buf_(?:1|2|4|6|8|12|16)', value):
            raise ValueError('invalid ' + key)
        cells[key] = value
    nets = (_json(config['FANOUT_REPAIR_NET_LIMITS'], 'FANOUT_REPAIR_NET_LIMITS')
              if 'FANOUT_REPAIR_NET_LIMITS' in config else None)
    direct = (_json(config['FANOUT_REPAIR_DIRECT_SINKS'], 'FANOUT_REPAIR_DIRECT_SINKS')
              if 'FANOUT_REPAIR_DIRECT_SINKS' in config else None)
    drivers = (_json(config['FANOUT_REPAIR_DRIVER_CELLS'], 'FANOUT_REPAIR_DRIVER_CELLS')
              if 'FANOUT_REPAIR_DRIVER_CELLS' in config else None)
    for net, value in (nets or {}).items():
        if not _name(net):
            raise ValueError('invalid targeted net name')
        _integer(value, 'FANOUT_REPAIR_NET_LIMITS', 2)
    normalized_direct = {} if direct is not None else None
    for net, pins in (direct or {}).items():
        if (not _name(net) or not isinstance(pins, list) or not pins
                or any(not _name(pin) or '/' not in pin or not all(pin.rsplit('/', 1)) for pin in pins)
                or len(set(pins)) != len(pins)
                or (nets is not None and len(pins) >= nets.get(net, limit))):
            raise ValueError('invalid FANOUT_REPAIR_DIRECT_SINKS')
        normalized_direct[net] = sorted(pins)
    if any(not _name(instance) or not _name(cell) for instance, cell in (drivers or {}).items()):
        raise ValueError('invalid FANOUT_REPAIR_DRIVER_CELLS')
    keep = (_integer(config['DIODE_TRIM_KEEP'], 'DIODE_TRIM_KEEP', 0)
            if 'DIODE_TRIM_KEEP' in config else None)
    minimum = (_integer(config['DIODE_TRIM_MIN_DIODES'], 'DIODE_TRIM_MIN_DIODES', 1)
               if 'DIODE_TRIM_MIN_DIODES' in config else None)
    if keep and minimum is not None and minimum <= keep:
        raise ValueError('invalid DIODE_TRIM_MIN_DIODES')
    topology = {'cells': cells, 'net_limits': nets, 'direct_sinks': normalized_direct, 'driver_cells': drivers}
    features.update(cells)
    features.update({
        'FANOUT_REPAIR_LIMIT': limit, 'FANOUT_REPAIR_ROUNDS': rounds,
        'DIODE_TRIM_KEEP': keep, 'DIODE_TRIM_MIN_DIODES': minimum,
        'repair_net_count': len(nets) if nets is not None else None,
        'repair_direct_net_count': len(direct) if direct is not None else None,
        'repair_direct_sink_count': sum(map(len, direct.values())) if direct is not None else None,
        'repair_driver_count': len(drivers) if drivers is not None else None,
        'repair_net_limit_sum': sum(nets.values()) if nets is not None else None,
        # An absent net-limit map leaves per-net branch headroom unknown.
        'repair_direct_headroom_known': 1 if direct is not None and (not direct or nets is not None) else None,
        'repair_topology_fingerprint': hashlib.sha256(_canonical(topology).encode()).hexdigest(),
        'repair_net_limits': _canonical(nets) if nets is not None else UNKNOWN_MAP,
        'repair_direct_sinks': _canonical(normalized_direct) if direct is not None else UNKNOWN_MAP,
        'repair_driver_cells': _canonical(drivers) if drivers is not None else UNKNOWN_MAP,
    })
    return features
