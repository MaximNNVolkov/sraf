"""Chronological M1 execution; OHLC ordering uncertainty stays explicit."""
from __future__ import annotations


def new_position(plan, price, at):
    sign = 1 if plan['direction'] == 'LONG' else -1
    if not (sign * (price - plan['stop']) > 0 and sign * (plan['tp1'] - price) > 0):
        return None
    return {'direction': plan['direction'], 'entry_price': price, 'entry_time': at,
            'initial_stop': plan['stop'], 'stop': plan['stop'], 'tp1': plan['tp1'], 'tp2': plan['tp2'],
            'remaining': 1.0, 'tp1_hit': False, 'status': 'ACTIVE', 'exits': [],
            'gross_pct': None, 'net_pct': None, 'outcome': None}


def _event(kind, bar, price=None, fraction=None, *, at_open=False):
    event = {'type': kind, 'time_from': bar['begin'], 'time_to': bar['begin'] if at_open else bar['end'],
             'time_precision': 'bar_open' if at_open else 'within_1m_bar', 'bar_timeframe': 1}
    if price is not None:
        event['price'] = price
    if fraction is not None:
        event['fraction'] = fraction
    return event


def exit_part(position, fraction, price, kind, bar, *, at_open=False):
    event = _event(kind, bar, price, fraction, at_open=at_open)
    sign = 1 if position['direction'] == 'LONG' else -1
    event['gross_pct_weighted'] = fraction * 100 * sign * (price / position['entry_price'] - 1)
    position['exits'].append(event)
    position['remaining'] = round(position['remaining'] - fraction, 8)
    if position['remaining'] == 0:
        gross = sum(e['gross_pct_weighted'] for e in position['exits'])
        position.update(status='CLOSED', gross_pct=gross, outcome='WIN' if gross > 1e-9 else 'LOSS' if gross < -1e-9 else 'BREAKEVEN',
                        exit_price=price, exit_time_from=event['time_from'], exit_time_to=event['time_to'],
                        weighted_exit_price=sum(e['price'] * e['fraction'] for e in position['exits']))
        risk = abs(position['entry_price'] - position['initial_stop']) / position['entry_price'] * 100
        position['r_multiple'] = gross / risk
    return event


def process_bar(position, bar):
    if position['status'] != 'ACTIVE':
        return []
    events = []
    sign = 1 if position['direction'] == 'LONG' else -1
    o, h, l = bar['open'], bar['high'], bar['low']

    def stop_hit(level):
        return l <= level if sign == 1 else h >= level

    def target_hit(level):
        return h >= level if sign == 1 else l <= level

    def ambiguous(reason):
        event = _event('AMBIGUOUS', bar)
        event['reason'] = reason
        position.update(status='AMBIGUOUS', outcome='UNRESOLVED', ambiguity=event)
        events.append(event)

    # The open precedes every other trade in this minute; stop gaps fill at open.
    if sign * (o - position['stop']) <= 0:
        events.append(exit_part(position, position['remaining'], o, 'STOP', bar, at_open=True))
        return events
    if not position['tp1_hit'] and sign * (o - position['tp1']) >= 0:
        events.append(exit_part(position, 0.5, position['tp1'], 'TP1', bar, at_open=True))
        position.update(tp1_hit=True, stop=position['entry_price'])
    if position['tp1_hit'] and sign * (o - position['tp2']) >= 0:
        events.append(exit_part(position, position['remaining'], position['tp2'], 'TP2', bar, at_open=True))
        return events
    target = position['tp2'] if position['tp1_hit'] else position['tp1']
    if stop_hit(position['stop']) and target_hit(target):
        ambiguous('Stop and target touched inside the same M1 bar; order unknown')
    elif stop_hit(position['stop']):
        events.append(exit_part(position, position['remaining'], position['stop'], 'STOP', bar))
    elif target_hit(target):
        if position['tp1_hit']:
            events.append(exit_part(position, position['remaining'], position['tp2'], 'TP2', bar))
        else:
            # TP1 is certain to have been reached; new BE-stop ordering may not be.
            events.append(exit_part(position, 0.5, position['tp1'], 'TP1', bar))
            position.update(tp1_hit=True, stop=position['entry_price'])
            if stop_hit(position['stop']):
                ambiguous('TP1 reached; order of new breakeven stop and remaining path is unknown')
            elif target_hit(position['tp2']):
                events.append(exit_part(position, position['remaining'], position['tp2'], 'TP2', bar))
    return events
