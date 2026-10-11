"""WORKED EXAMPLE of a complete phone agent generator (several items per customer, one promise per item, optional notes).

This file is not a rule book. It shows a design that handled its practice customers well: a hybrid workflow of conversation
nodes, code nodes, hooks and tool-call transitions, with per-item tracking, a fixed read-back, and a close tool that fires at
the end of every call. Its prompts and numbers belong to the practice brief it was written for.

How to use it for a new brief (do NOT copy a single rule, number, date, name or outcome code from here):
  1. Copy it to out/gen_<slot>.py and read it once, top to bottom.
  2. Keep the architecture, the hooks (what_to_do_next, tracking, finish) and the plumbing.
  3. Replace EVERY brief-specific part with what the new brief says. Search for the markers `# EDIT:` below, then read the
     prompts (T_*, *_ENTRY) line by line against your rules.md and rewrite each sentence that the new brief changes:
     holidays and weekend, bands and deadlines, number of attempts, floor amounts, callback window, identity questions and
     their order, wrong-answer limit, field names and enum values of the tool API file, outcome table and goodbye texts,
     whether notes are allowed, which transfers exist, required spoken sentences.
  4. If the brief forbids a tool that this example uses (for instance notes), remove the tool, its hooks and every sentence that
     mentions it, and settle each item through the transitions instead.
  5. Run it as `python3 out/gen_<slot>.py <slot>`; it writes out/<slot>.workflow.json.
"""
import json, pathlib, sys

# EDIT: base URL of the bank API (servers entry of the tool API file)
BASE = "https://freya-bank-mock.freyavoice.workers.dev/v1"
HDR = {"type": "object", "properties": {"Content-Type": {"type": "string", "default": "application/json"}}}

# ---------------------------------------------------------------- python snippets (no double braces allowed)
CALC = r'''
import datetime, json, re
    # EDIT: holidays and weekend of the brief (the weekday test below is Monday to Friday)
HOL = set(['2026-10-26', '2026-11-11', '2026-11-26', '2026-12-25', '2027-01-01'])
ONE = datetime.timedelta(days=1)
def isbd(d):
    return d.weekday() < 5 and d.isoformat() not in HOL
def nth_bd(d0, n):
    d = d0
    k = 0
    while k < n:
        d = d + ONE
        if isbd(d):
            k += 1
    return d
def say(d):
    return d.strftime('%A') + ' ' + str(d.day) + ' ' + d.strftime('%B %Y') + ' (' + d.isoformat() + ')'
def money(x):
    try:
        return '%.2f' % float(x)
    except Exception:
        return str(x)
def last_bd_of_week(monday):
    d = monday + datetime.timedelta(days=4)
    while not isbd(d):
        d = d - ONE
    return d
try:
    s = str(vars.get('now') or '')
    m = re.match(r'(\d{4})-(\d{1,2})-(\d{1,2})(?:[T ](\d{1,2}):(\d{2}))?', s)
    today = datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    hh = int(m.group(4) or 10)
    mi = int(m.group(5) or 0)
except Exception:
    today = datetime.date.today()
    hh, mi = 10, 0
try:
    monday = today - datetime.timedelta(days=today.weekday())
    names = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
    lines = []
    lines.append('Today is ' + say(today) + ', time ' + '%02d:%02d' % (hh, mi) + '. Use this, never the real clock.')
    lines.append('Bank holidays (not business days): 2026-10-26, 2026-11-11, 2026-11-26, 2026-12-25, 2027-01-01. Business days are Monday to Friday except these.')
    lines.append('"this <weekday>" = that day in the current week; "next <weekday>" = that day in the following calendar week:')
    for i in range(7):
        lines.append('  this ' + names[i] + ' = ' + (monday + datetime.timedelta(days=i)).isoformat() + ', next ' + names[i] + ' = ' + (monday + datetime.timedelta(days=7 + i)).isoformat())
    lines.append('Day by day:')
    for i in range(0, 36):
        d = today + datetime.timedelta(days=i)
        tag = 'business day' if isbd(d) else ('BANK HOLIDAY' if d.isoformat() in HOL else 'weekend')
        extra = ' (today)' if i == 0 else (' (tomorrow)' if i == 1 else '')
        lines.append('  ' + say(d) + extra + ': ' + tag)
    vars['calendar_facts'] = '\n'.join(lines)
    vars['today_iso'] = today.isoformat()
    nbd = nth_bd(today, 1)
    # EDIT: callback window and rules of the brief
    cb = []
    if hh < 19:
        cb.append('today ' + say(today) + ' at a time later than ' + '%02d:%02d' % (hh, mi) + ' and no later than 19:00')
    cb.append(say(nbd) + ' (next business day) from 10:00 to 19:00, both included')
    vars['callback_facts'] = 'Valid callback slots, nothing else: ' + '; or '.join(cb) + '. Any other day (including later days) or any time before 10:00 or after 19:00 is outside the window.'
    a1 = last_bd_of_week(monday)
    if a1 <= today:
        a1 = last_bd_of_week(monday + datetime.timedelta(days=7))
except Exception as e:
    vars['calendar_facts'] = 'Today: ' + str(vars.get('now'))
    vars['callback_facts'] = 'Today or the next business day, 10:00 to 19:00.'
    a1 = today

# ----- account facts (only when get_account has run)
def find_products():
    v = vars.get('acct_products')
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except Exception:
            try:
                v = json.loads(v.replace("'", '"').replace('True', 'true').replace('False', 'false').replace('None', 'null'))
            except Exception:
                v = None
    if isinstance(v, dict):
        v = [v]
    if isinstance(v, list) and v and isinstance(v[0], dict):
        return v
    found = {}
    for k in list(vars.keys()):
        mm = re.search(r'products(?:\.|\[)(\d+)\]?\.(\w+)$', str(k))
        if mm:
            found.setdefault(int(mm.group(1)), {})[mm.group(2)] = vars.get(k)
    if found:
        return [found[i] for i in sorted(found)]
    one = {}
    for f in ['product_id', 'type', 'card_last4', 'past_due', 'days_late', 'minimum_payment', 'instalment_amount']:
        if vars.get('acct_' + f) not in (None, ''):
            one[f] = vars.get('acct_' + f)
    return [one] if one.get('past_due') not in (None, '') else []
try:
    if vars.get('fetch_account_success_count') or vars.get('acct_products') or vars.get('acct_past_due'):
        prods = find_products()
        if not prods:
            raise ValueError('no products')
        f = []
        stm = []
        pids = []
        phrases = []
        n = len(prods)
        for i, p in enumerate(prods):
            typ = str(p.get('type') or '')
            pid = str(p.get('product_id') or ('p' + str(i + 1)))
            full = float(p.get('past_due'))
            late = int(float(p.get('days_late')))
            # EDIT: product types, how each is named aloud, and which field is its floor amount (all from the tool API file and the brief)
            if typ == 'credit_card':
                phrase = 'credit card ending ' + str(p.get('card_last4') or '')
                floor = float(p.get('minimum_payment'))
                floor_name = 'minimum payment'
            else:
                phrase = 'car loan' if typ == 'car_loan' else 'personal loan'
                floor = float(p.get('instalment_amount'))
                floor_name = 'instalment amount'
            # EDIT: bands, deadlines (business days) and attempts per band, all from the brief
            early = late <= 7
            deadline = nth_bd(today, 12 if early else 7)
            valid = []
            d = today
            while d <= deadline:
                if isbd(d):
                    valid.append(d)
                d = d + ONE
            pids.append(pid)
            phrases.append(phrase)
            vars['phrase_' + pid] = phrase
            stm.append('On your ' + phrase + ', the past-due amount is ' + money(full) + ' and it is ' + str(late) + ' days late.')
            f.append('PRODUCT ' + str(i + 1) + ' of ' + str(n) + ': ' + phrase + ' (product_id ' + pid + ').')
            f.append('  Full past-due amount: ' + money(full) + '. Days late: ' + str(late) + '.')
            f.append('  Floor amount (the ' + floor_name + '): ' + money(floor) + '. The lowest amount that can be recorded for this product.')
            f.append('  Band: ' + ('EARLY (1 to 7 days late)' if early else 'LATE (8 to 45 days late)') + '. Persuasion attempts allowed for this product: ' + ('1' if early else '2') + '.')
            f.append('  Deadline (latest promise date for this product): ' + say(deadline) + '.')
            f.append('  Valid promise dates for this product (only these, today included): ' + ', '.join(say(x) for x in valid) + '.')
            f.append('  Attempt 1 for this product: propose paying on ' + say(a1) + ' (the full amount, or the floor ' + money(floor) + ' if the customer already said the full amount is too much for this product).')
            if early:
                f.append('  There is NO attempt 2 for this product. After attempt 1 is refused, this product gets no promise (note it as refused at the end).')
            else:
                f.append('  Attempt 2 for this product: propose the floor amount ' + money(floor) + ' on the deadline ' + say(deadline) + '.')
        vars['pid_list'] = ','.join(pids)
        vars['n_products'] = str(n)
        vars['product_id'] = pids[0]
        vars['product_phrase'] = phrases[0]
        intro = 'I am calling from the collections team about late payments. ' if n > 1 else 'I am calling from the collections team about a late payment. '
        ask = ' Can you pay all of them today?' if n > 1 else ' Can you make this payment today?'
        vars['products_statement'] = intro + ' '.join(stm) + ask
        vars['account_facts'] = '\n'.join(f)
        vars['acct_ok'] = 'yes'
except Exception as e:
    vars['acct_ok'] = 'no'
    vars['account_facts'] = 'Account data could not be read.'
'''


def close_code(outcome, reason, farewell):
    # farewell may contain TITLE/LAST placeholders filled from vars
    return (
        "try:\n"
        f"    vars['close_outcome'] = {outcome!r}\n"
        f"    vars['close_reason'] = {reason!r}\n"
        f"    t = {farewell!r}\n"
        "    who = (str(vars.get('title') or '') + ' ' + str(vars.get('last_name') or '')).strip()\n"
        "    t = t.replace('WHO', who)\n"
        "    vars['farewell'] = t\n"
        "except Exception:\n"
        f"    vars['farewell'] = 'Thank you for your time. Goodbye.'\n"
    )


def code_node(name, code, target, extra_transitions=None):
    return {
        "name": name, "node_type": "code", "passthrough": True, "passthrough_default_transition": target,
        "on_enter": {"type": "silent"}, "disable_inactivity_monitor": True,
        "pre_actions": [{"type": "function", "function": {"name": "code_exec", "handler": "__function__:code_exec",
                         "parameters": {"type": "object", "properties": {
                             "code": {"type": "string", "default": code},
                             "timeout_ms": {"type": "number", "default": 10000}}}}}],
        "transitions": (extra_transitions or []) + [{"target_node": target, "description": "always", "condition": {"operator": "always"}}],
    }


def api_tool(name, desc, body_props, required, retries=0, mapping=None, post=None, pre=None):
    props = {"customer_id": {"type": "string", "default": "{{customer_id}}"}}
    props.update(body_props)
    t = {"type": "function", "config": {"timeoutMs": 12000, "maxRetries": retries},
         "function": {"name": name, "handler": "__function__:api_call", "description": desc,
                      "parameters": {"type": "object", "properties": {
                          "url": {"type": "string", "default": f"{BASE}/{name}"},
                          "method": {"type": "string", "default": "POST"},
                          "headers": HDR,
                          "body": {"type": "object", "properties": props, "required": required}}}}}
    if mapping: t["response_mapping"] = mapping
    if post: t["post_process_code"] = post
    if pre: t["pre_process_code"] = pre
    return t


def cond_vals(var, vals):
    return {"logic": "or", "conditions": [{"variable": var, "operator": "equals", "operand": v} for v in vals]}


def is_true(*vs):
    return {"logic": "or", "conditions": [{"variable": v, "operator": "equals", "operand": o} for v in vs for o in ("true", "True")]}


def tt(target, desc):
    """tool-call transition: the dialogue model calls transition_to_<target>"""
    return {"target_node": target, "description": desc, "is_tool_call": True,
            "condition": {"operator": "matches_intent", "description": desc}}


STRIP = "try:\n    if isinstance(result, dict):\n        result.pop('header', None)\nexcept Exception:\n    pass\n"

VERIFY_POST = r'''
try:
    if isinstance(result, dict):
        result.pop('header', None)
        d = result.get('data')
        if not isinstance(d, dict):
            d = {}
            result['data'] = d
        left = d.get('wrong_answers_left')
        if d.get('hint') == 'day_month_swapped':
            msg = 'Day and month look swapped. This is NOT counted as a wrong answer. Ask the customer to say their date of birth again with the month as a word (for example "14 March 1988").'
        elif d.get('ok') is False and d.get('verified') is None:
            msg = 'The answer could not be read and was not counted. Ask the customer to repeat it (date of birth with the month as a word).'
        elif d.get('all_verified') is True:
            msg = 'Both security answers are verified.'
        elif d.get('verified') is True:
            msg = 'Correct. Now ask for the last four digits of the phone number on file.'
        elif str(left) == '0':
            msg = 'No attempts left. The identity check has failed.'
        else:
            msg = 'That answer is wrong. Wrong answers left: ' + str(left) + '. Tell the customer it does not match and ask the SAME question again. Never reveal the correct answer.'
        d['what_to_do_next'] = msg
except Exception:
    pass
'''

INIT = r'''
vars['promised_pids'] = ''
vars['noted'] = ''
vars['done_pids'] = ''
vars['b_all_done'] = 'no'
vars['fail_pids'] = ''
'''

# shared helper text used by both post-hooks (no double braces)
TRACK = r'''
def _lst(k):
    return [x for x in str(vars.get(k) or '').split(',') if x]
def _remaining():
    allp = _lst('pid_list')
    done = _lst('done_pids')
    return [x for x in allp if x not in done]
def _mark_done(pid):
    done = _lst('done_pids')
    if pid and pid not in done:
        done.append(pid)
    vars['done_pids'] = ','.join(done)
    rem = _remaining()
    if _lst('pid_list') and not rem:
        vars['b_all_done'] = 'yes'
    return rem
def _names(pids):
    return ', '.join(str(vars.get('phrase_' + x) or x) + ' (product_id ' + x + ')' for x in pids) or 'none'
'''

PROMISE_PRE = r'''
try:
    b = config.get('body')
    if isinstance(b, dict):
        vars['last_promise_pid'] = str(b.get('product_id') or '')
except Exception:
    pass
'''

PROMISE_POST = TRACK + r'''
try:
    if isinstance(result, dict):
        result.pop('header', None)
        d = result.get('data')
        sc = result.get('status_code')
        pid = str(vars.get('last_promise_pid') or vars.get('create_promise_args.product_id') or '')
        good = isinstance(d, dict) and d.get('ok') is True and (sc is None or str(sc) == '200')
        if not isinstance(d, dict):
            d = {'raw': str(d)[:200]}
            result['data'] = d
        if good:
            pr = _lst('promised_pids')
            if pid and pid not in pr:
                pr.append(pid)
            vars['promised_pids'] = ','.join(pr)
            rem = _mark_done(pid)
            if rem:
                d['what_to_do_next'] = 'Recorded for ' + _names([pid]) + '. Products still without a promise or a note: ' + _names(rem) + '. If the customer confirmed a promise for one of them, call create_promise for it now. For each product with no promise, call add_note with its reason. If nothing is left to call, tell the customer in one short sentence that you are finishing.'
            else:
                d['what_to_do_next'] = 'Recorded. All products are done.'
        else:
            d['ok'] = False
            fl = _lst('fail_pids')
            if pid in fl:
                d['what_to_do_next'] = 'NOT recorded, second failure for this product. Do NOT say it is recorded. Call add_note now with product_id ' + pid + ' and reason service_error, then continue with the other products.'
            else:
                fl.append(pid)
                vars['fail_pids'] = ','.join(fl)
                d['what_to_do_next'] = 'NOT recorded. Do NOT say it is recorded. Call create_promise again right now with exactly the same product_id, amount and date, without asking the customer.'
except Exception:
    pass
'''

NOTE_PRE = r'''
try:
    b = config.get('body')
    if isinstance(b, dict):
        vars['last_note_pid'] = str(b.get('product_id') or '')
        vars['last_note_reason'] = str(b.get('reason') or '')
except Exception:
    pass
'''

NOTE_POST = TRACK + r'''
try:
    if isinstance(result, dict):
        result.pop('header', None)
        d = result.get('data')
        if not isinstance(d, dict):
            d = {}
            result['data'] = d
        pid = str(vars.get('last_note_pid') or vars.get('add_note_args.product_id') or '')
        reason = str(vars.get('last_note_reason') or vars.get('add_note_args.reason') or '')
        if d.get('ok') is True:
            nt = [x for x in str(vars.get('noted') or '').split(';') if x]
            if not any(x.split(':')[0] == pid for x in nt):
                nt.append(pid + ':' + reason)
            vars['noted'] = ';'.join(nt)
            rem = _mark_done(pid)
            if rem:
                d['what_to_do_next'] = 'Note saved. Products still without a promise or a note: ' + _names(rem) + '. Handle them.'
            else:
                d['what_to_do_next'] = 'Note saved. All products are done.'
        else:
            d['what_to_do_next'] = 'The note was not saved. Call add_note once more with the same values.'
except Exception:
    pass
'''

FINISH = r'''
try:
    allp = [x for x in str(vars.get('pid_list') or '').split(',') if x]
    pr = [x for x in str(vars.get('promised_pids') or '').split(',') if x]
    nt = {}
    for x in str(vars.get('noted') or '').split(';'):
        if ':' in x:
            nt[x.split(':')[0]] = x.split(':')[1]
    who = (str(vars.get('title') or '') + ' ' + str(vars.get('last_name') or '')).strip()
    bye = ' Thank you for your time, ' + who + '. Goodbye.'
    if not pr:
        try:
            if int(float(vars.get('create_promise_success_count') or 0)) > 0:
                pr = ['x']
        except Exception:
            pass
    if pr:
        vars['close_outcome'] = 'promise'
        vars['close_reason'] = 'none'
        names = ' and '.join(str(vars.get('phrase_' + x) or 'your product') for x in pr if x != 'x') or 'your product'
        fail = [x for x in allp if nt.get(x) == 'service_error']
        t = 'Your promise for the ' + names + ' is recorded. You will receive a text message confirming this promise.'
        if fail:
            t = t + ' I am sorry, I could not record the promise for the ' + ' and '.join(str(vars.get('phrase_' + x) or x) for x in fail) + ' because of a technical problem.'
        vars['farewell'] = t + bye
    else:
        reasons = [nt.get(x) for x in allp if nt.get(x)]
        if allp and reasons and len(reasons) == len(allp) and all(r == 'paid' for r in reasons):
            vars['close_outcome'] = 'paid'
            vars['close_reason'] = 'none'
            vars['farewell'] = 'Thank you. Payments can take one business day to appear.' + bye
        else:
            first = 'refused'
            for x in allp:
                r = nt.get(x)
                if r and r != 'paid':
                    first = r
                    break
            if not reasons:
                lr = str(vars.get('last_note_reason') or '')
                if lr in ('refused', 'hardship', 'dispute', 'service_error'):
                    first = lr
            vars['close_outcome'] = 'no_promise'
            vars['close_reason'] = first
            if first == 'service_error':
                vars['farewell'] = 'I am sorry, I could not record your promise because of a technical problem.' + bye
            else:
                vars['farewell'] = 'I understand.' + bye
except Exception:
    vars['close_outcome'] = 'no_promise'
    vars['close_reason'] = 'refused'
    vars['farewell'] = 'Thank you for your time. Goodbye.'
'''

CALLBACK_POST = r'''
try:
    if isinstance(result, dict):
        result.pop('header', None)
        d = result.get('data')
        if isinstance(d, dict) and d.get('ok') is True:
            d['what_to_do_next'] = 'The callback is booked.'
        else:
            if not isinstance(d, dict):
                d = {}
                result['data'] = d
            d['ok'] = False
            d['what_to_do_next'] = 'NOT booked. Do not say it is booked. Try schedule_callback once more with the same values.'
except Exception:
    pass
'''

CLOSE_PRE = r'''
try:
    b = config.get('body')
    if isinstance(b, dict) and (b.get('outcome') in ('promise', 'paid') or str(b.get('reason') or '').strip() in ('', 'none')):
        b.pop('reason', None)
except Exception:
    pass
'''

import sys as _s, pathlib as _p; _s.path.insert(0, str(_p.Path(__file__).resolve().parent) if (_p.Path(__file__).resolve().parent/'freya_kit.py').exists() else str(_p.Path(__file__).resolve().parent.parent/'examples'))
from freya_kit import cond_vals, is_true, tt, code_node, close_code, UNIVERSAL_RULES
# ---------------------------------------------------------------- prompts
# EDIT: system prompt: bank name, global rules (keep the rules of freya_kit.UNIVERSAL_RULES)
SYSTEM = """You are Freya Bank's digital assistant on an outbound phone call to {{title}} {{last_name}} ({{first_name}} {{last_name}}). Speak English. One or two short sentences per turn. Address the customer as {{title}} {{last_name}}.
Global rules:
- Current date and time: {{now}}. Use it for every date; never use the real clock.
- If asked whether you are a robot or an AI: say you are Freya Bank's digital assistant, then continue.
- If asked whether this is a scam: say they can verify the call by phoning Freya Bank's customer service number, then continue.
- A question you cannot answer from the information you have: say you do not have that information. Never invent figures, dates or reasons.
- Never offer a discount, a waiver, an instalment plan or any other term.
- After every tool result, either speak to the customer or call the next tool. Never end a turn with nothing. Call one tool per message. When a rule says to call a tool, call it; saying it is not enough.
- When a tool result contains what_to_do_next, follow it.
- Never say goodbye yourself: when the call has to end, call the matching transition tool and the closing words are spoken for you."""
SYSTEM = SYSTEM + "\n" + UNIVERSAL_RULES   # tested rules of the kit: cut-off messages, tool discipline, safety, no repeated figures

T_OPENING = """You have said the opening and asked whether you are speaking with {{title}} {{last_name}}. Do not say why you are calling, and never say collections, debt, payment, amount or any product. If the person asks a question, answer it briefly (robot or AI: you are Freya Bank's digital assistant; scam: they can verify by phoning Freya Bank's customer service number; what it is about: you can only discuss it with {{title}} {{last_name}}) and ask again: "Am I speaking with {{title}} {{last_name}}?". No tool call is allowed in this step."""

REASK_ENTRY = """Only if the person really asked a question in their last message (never invent one), answer it in one short sentence first (robot or AI: you are Freya Bank's digital assistant; scam: they can verify the call by phoning Freya Bank's customer service number; who you are or what it is about: you are Freya Bank's digital assistant and can only discuss it with {{title}} {{last_name}}). Then ask once more: "Am I speaking with {{title}} {{last_name}}?". Do not say why you are calling. No tool call."""

T_REASK = """You asked a second time whether you are speaking with {{title}} {{last_name}}. Do not say why you are calling and never mention collections, debt, payment or any product. If the person asks something, answer briefly and wait. No tool call is allowed in this step."""

T_THIRD = """The person on the line is NOT {{title}} {{last_name}}. Your only job is to find out when {{title}} {{last_name}} can be reached.
Rules:
- Never say why you are calling. Never mention collections, a debt, a payment, an amount, a product or the bank account. If asked, say you can only discuss it with {{title}} {{last_name}} personally. If they offer to pay or to answer questions for the customer, politely say you can only speak with {{title}} {{last_name}}. Do not ask them any security question.
- Ask when {{title}} {{last_name}} can be reached (day and time).
- {{callback_facts}}
- When they give a day and a time inside the valid slots, call schedule_callback with date as YYYY-MM-DD and time as HH:MM (24 hour; "eleven in the morning" = 11:00, "around three in the afternoon" = 15:00). If they give only a day or only a vague time ("morning"), ask for the exact time.
- If the day or time is outside the valid slots: tell them the callback can only be today or {{callback_facts_short}} between 10:00 and 19:00, and ask ONCE for another time. If the second answer is also outside, or they cannot give any time, call transition_to_close_no_callback.
- If they say this number does not belong to {{first_name}} {{last_name}}: call transition_to_close_wrong_number.
- If they say {{first_name}} {{last_name}} has died: call transition_to_close_deceased immediately, without asking anything.
Calendar:
{{calendar_facts}}"""

IDENTITY_ENTRY = """The customer confirmed they are {{title}} {{last_name}}. Only if they really asked a question in their last message (never invent one: do not mention robots, scams or the bank's phone number unless asked), answer it in one short sentence (robot: you are Freya Bank's digital assistant; scam: verify by phoning Freya Bank's customer service number; what it is about: you can explain right after a short security check). Then say you need to ask two security questions and ask for their date of birth. Do not mention collections, debt, payment, an amount or a product. No tool call in this turn."""

# EDIT: identity questions, order, formats, wrong-answer limit, what to do on refusal (all from the brief)
T_IDENTITY = """You are checking the identity of {{title}} {{last_name}}. Until both answers pass, never mention collections, a debt, a payment, an amount or a product, and never say why you are calling beyond "about your Freya Bank account".
Steps:
1. Ask for the date of birth. When the customer answers, call verify_identity with field "date_of_birth" and answer as YYYY-MM-DD, whatever way they said it ("14 March 1988" -> 1988-03-14). A date said only in numbers is day, then month, then year ("03/07/1990" -> 1990-07-03).
2. Then ask for the last four digits of the phone number on file and call verify_identity with field "phone_last4" and the four digits.
2b. Customers often give an answer in parts over several messages ("Nineteen October." then "Nineteen eighty-three."; "Forty-four." then "Zero-one."). A part is not an answer and not a wrong answer: reply only "Please go on." (do not repeat the question), then JOIN the parts in order ("forty-four" + "zero-one" = 4401) and call verify_identity with the joined value. If the customer corrects themselves, use the last value. Numbers said in words are fine.
3. Call verify_identity once for every answer the customer gives, even if you think it is wrong. Ask one question at a time. Follow what_to_do_next in the result: a wrong answer -> ask the same question again; a day_month_swapped hint -> ask them to say the month as a word (this does not count as wrong).
4. Count a refusal only when the customer clearly says they will not answer or do not want the check, or when a message that has NO answer in it asks what the call is about instead of answering. Chat, stories, cut-off messages and unrelated questions are NOT refusals: reply with one short neutral line and ask the pending question again. The first real refusal: explain once that these questions protect their account and ask again. The second real refusal: call transition_to_close_declined. If they say twice that they do not want to talk, call transition_to_close_declined.
5. If they ask for a human: ask once whether you can help first. If they insist, call transition_to_transfer_human.
6. If they ask how much they owe or what the call is about while answering, say you can tell them right after the security check."""

NEGOTIATE_ENTRY = "Thank you, {{title}} {{last_name}}, you are verified. {{products_statement}}"

# EDIT: negotiation rules: attempts, offers, dates, amounts, hardship, special requests, read-back, notes
T_NEGOTIATE = """The customer {{title}} {{last_name}} is verified. You have already stated every late product and asked whether they can pay today (all of them, if there are several).

ACCOUNT FACTS (use exactly these values; each product has its OWN floor, band, deadline and attempts):
{{account_facts}}

TOOL RULES
- create_promise: call it ONLY after ONE read-back of ALL agreed promises together (each product with its amount and date) followed by "Do you confirm?", and the customer clearly said yes. Then call create_promise once per agreed product, one call per message, product_id copied exactly from the facts, amount as a number, date as YYYY-MM-DD. Never say a promise is recorded yourself; the confirmation is spoken automatically at the end.
- If create_promise fails, call it once more with the same values. If it fails a second time, call add_note for that product with reason service_error. Keep the promises that were recorded.
- add_note: for EVERY product that ends without a promise, call add_note with its product_id and reason: refused (attempts used up or flat refusal / does not want to talk), hardship (illness, death in the family or job loss and declined the support transfer), dispute (says the product or debt is not theirs, or it was closed or paid off in the past), paid (says they already paid this past-due amount), service_error (create_promise failed twice). Call add_note ONLY at the very end, after every product is settled and every confirmed promise is recorded. Never call add_note when the call is being transferred.
- Transfers (call them without saying goodbye; no add_note before or after): transition_to_transfer_restructuring, transition_to_transfer_support, transition_to_transfer_human. Promises not yet recorded are not recorded when a transfer happens.
- Never say goodbye yourself. When the last product is recorded or noted, the call ends automatically.

GETTING THE PROMISES
0. If there are several products and the customer cannot pay all today, go through the products one by one in the order of the facts. Do NOT ask about today again for each product. Each product starts at its own attempt 1 and has its own attempt count. If the customer gives one date (or a plan) for all products, apply it to every product at once.
1. Yes to paying today -> the promise is the full past-due amount, today, for each product concerned. Go to the read-back.
2. A date or amount the customer proposes is NEVER a refusal. Check it against THAT product's facts:
   Date: it must be one of that product's valid promise dates. A weekend day or bank holiday on or before the deadline -> offer the next business day after it. A date after that product's deadline -> that product can only be recorded on its own deadline: say the latest date you can record for it is the deadline and offer it; if they refuse the deadline, that is a flat refusal for that product. If the customer named one date for all products, the other products keep that date, and the read-back says which product is on its own deadline. A vague period ("next week", "end of the month", "when my salary comes") -> ask once for the exact day; if they still give none, propose the deadline. "next <weekday>" and "this <weekday>": use the calendar below.
   Amount: from that product's floor up to its full amount, both included -> accept it. Below the floor -> say it cannot be recorded and offer the floor once; if they do not accept the floor, that is a flat refusal.
3. You propose every amount. Ask for the full past-due amount first. Never ask "how much can you pay?".
4. If the customer says the full amount of a product is too much: offer that product's floor ONCE: "I can record the <minimum payment / instalment amount> of <floor> instead; the rest stays overdue." Refusing the floor is a flat refusal. Afterwards never say that floor figure again until the read-back and never propose the full amount again; refer to it as "that payment" and only change the date.
5. A flat refusal or a non-answer like "you tell me" uses one persuasion attempt of the current product. A no with no date and no amount is a flat refusal.
   - Attempt 1: propose the product's attempt-1 date (full amount, or "that payment" if the floor was already offered).
   - Attempt 2 (LATE band products only): propose the floor on that product's deadline.
   - ONE proposal per attempt, nothing else. After the LAST allowed attempt of a product is refused, say only "I understand." and go on to the next product. Do NOT ask the customer to suggest an amount or a date, do NOT ask another question and do NOT make a further offer for that product: each extra proposal or question counts as one more attempt and breaks the limit. An early-band product gets exactly its one attempt, a late-band product exactly its two; count the proposals you have made for THAT product before you speak.
   - When the product's attempts are used up and the customer still refuses, stop for that product (it will be noted refused) and move to the next product. Never repeat an offer the customer already refused. Never repeat a figure you already said.
6. Read-back once all products are settled: list every agreed promise "<product>, <amount>, on <weekday day month year>" (say when a product is on its own deadline) and ask "Do you confirm?". If the customer changes a date or amount, check it and read back again.
7. A cut-off, empty or unclear message is not an answer and not a refusal: say ONLY "Sorry, I didn't catch that, please go on." and wait. Never repeat your offer or your question in that line (a repeated offer counts as a second offer, for example a second offer to connect the customer with the support team). Answer side questions in one sentence and return to the pending step.

OTHER SITUATIONS (these come before the persuasion steps)
- Illness, a death in the family or job loss: say exactly ONE short sentence of sympathy (once in the whole call), make NO persuasion attempt, and offer to connect them with the customer support team. Accepts -> transition_to_transfer_support. Declines -> every product without an agreed promise gets reason hardship. If they still name a date or an amount themselves, take that promise as above.
- "I already paid" a product: say payments can take one business day to appear; no promise for that product (reason paid); continue with the other products.
- "This is not mine", "I never had this product", or it was closed or paid off in the past: do not argue; no promise for that product (reason dispute); continue with the others.
- A new payment plan or lower instalments: do not describe any plan; call transition_to_transfer_restructuring.
- A request for a human: ask once whether you can help first. If they insist, call transition_to_transfer_human.
- The customer says a second time that they do not want to talk: every product without a recorded promise gets reason refused.
- Never offer a discount, waiver or plan. One promise per product. A recorded promise cannot be changed; they can change it in the mobile app.

CALENDAR
{{calendar_facts}}"""

# ---------------------------------------------------------------- nodes
nodes = {}

nodes["setup"] = code_node("setup", INIT + CALC, "opening")

WHO_ENUM = ["confirmed", "other_person", "wrong_number", "deceased", "refuses", "unclear"]
WHO_DESC = ("How the person answered the question whether they are the customer the agent asked for. "
            "confirmed: ONLY a clear yes, 'that's me', 'speaking', or they say the customer's own name as theirs. "
            "other_person: they are someone else (a relative, partner, colleague), the customer is not there or not available, or they offer to take a message. "
            "wrong_number: the number does not belong to the customer / they do not know that person. "
            "deceased: the customer has died. refuses: they do not want to talk. "
            "unclear: anything else, e.g. 'hello', 'go ahead', 'who is this', 'what is this about', a question, or no answer.")


def who_transitions(var, unclear_target, refuses_target):
    return [
        {"target_node": "close_deceased", "description": "customer died", "condition": cond_vals(var, ["deceased"])},
        {"target_node": "close_wrong_number", "description": "wrong number", "condition": cond_vals(var, ["wrong_number"])},
        {"target_node": "third_party", "description": "someone else answered", "condition": cond_vals(var, ["other_person"])},
        {"target_node": "identity", "description": "customer confirmed", "condition": cond_vals(var, ["confirmed"])},
        {"target_node": refuses_target, "description": "refuses to talk", "condition": cond_vals(var, ["refuses"])},
        {"target_node": unclear_target, "description": "unclear", "condition": cond_vals(var, ["unclear"])},
    ]


nodes["opening"] = {
    "name": "opening", "node_type": "conversation",
    "on_enter": {"type": "tts", "text": "Hello, I am a digital assistant calling from Freya Bank, and this call is recorded. Am I speaking with {{title}} {{last_name}}?"},
    "on_enter_first_entry_only": True,
    "task_message": T_OPENING,
    "extraction_schema": [{"name": "who_1", "type": "string", "enum_values": WHO_ENUM, "description": WHO_DESC, "min_confidence": 0.5}],
    "max_node_turns": 6, "max_turn_transition": "close_unclear",
    "transitions": who_transitions("who_1", "reask", "reask") + [
        {"target_node": "reask", "description": "no clear answer", "condition": {"operator": "always"}}],
}

nodes["reask"] = {
    "name": "reask", "node_type": "conversation",
    "on_enter": {"type": "llm", "prompt": REASK_ENTRY},
    "on_enter_first_entry_only": True,
    "task_message": T_REASK,
    "extraction_schema": [{"name": "who_2", "type": "string", "enum_values": WHO_ENUM, "description": WHO_DESC, "min_confidence": 0.5}],
    "max_node_turns": 4, "max_turn_transition": "close_unclear",
    "transitions": who_transitions("who_2", "close_unclear", "close_declined"),
}

nodes["third_party"] = {
    "name": "third_party", "node_type": "conversation",
    "on_enter": {"type": "llm", "prompt": "The person is not {{title}} {{last_name}}. Do not say why you are calling. If they asked what it is about, say you can only discuss it with {{title}} {{last_name}} personally. If they already gave a day and time when {{title}} {{last_name}} can be reached and it is inside the valid slots ({{callback_facts}}), call schedule_callback now. Otherwise ask when {{title}} {{last_name}} can be reached. If they said the customer has died, call transition_to_close_deceased. If they said the number is not the customer's, call transition_to_close_wrong_number."},
    "on_enter_first_entry_only": True,
    "task_message": T_THIRD,
    "tools": [api_tool("schedule_callback", "Book a callback. CALL it as soon as the person gives a day and a time inside the valid callback slots.",
                       {"date": {"type": "string", "description": "Callback date, YYYY-MM-DD", "default": "{{cb_date}}"},
                        "time": {"type": "string", "description": "Callback time, HH:MM 24 hour, between 10:00 and 19:00", "default": "{{cb_time}}"}},
                       ["date", "time"], mapping=[{"path": "data.ok", "variable": "callback_ok"}], post=CALLBACK_POST)],
    "max_node_turns": 12, "max_turn_transition": "close_no_callback",
    "transitions": [
        {"target_node": "close_callback", "description": "callback booked", "condition": is_true("callback_ok", "schedule_callback_response.data.ok")},
        tt("close_no_callback", "No callback inside the allowed window could be agreed after asking once more"),
        tt("close_wrong_number", "The person says this number does not belong to the customer"),
        tt("close_deceased", "The person says the customer has died"),
    ],
}

nodes["identity"] = {
    "name": "identity", "node_type": "conversation",
    "on_enter": {"type": "llm", "prompt": IDENTITY_ENTRY},
    "on_enter_first_entry_only": True,
    "task_message": T_IDENTITY,
    "tools": [api_tool("verify_identity", "Check ONE security answer. CALL it every time the customer gives a date of birth or phone digits.",
                       {"field": {"type": "string", "enum": ["date_of_birth", "phone_last4"], "description": "Which question was answered: date_of_birth or phone_last4.", "default": "{{field}}"},
                        "answer": {"type": "string", "description": "date_of_birth as YYYY-MM-DD; phone_last4 as four digits.", "default": "{{answer}}"}},
                       ["field", "answer"],
                       mapping=[{"path": "data.all_verified", "variable": "id_all_verified"}, {"path": "data.wrong_answers_left", "variable": "id_left"}],
                       post=VERIFY_POST)],
    "max_node_turns": 36, "max_turn_transition": "close_declined",
    "transitions": [
        {"target_node": "fetch_account", "description": "both answers verified", "condition": is_true("id_all_verified", "verify_identity_response.data.all_verified")},
        {"target_node": "close_failed", "description": "three wrong answers", "condition": cond_vals("id_left", ["0"])},
        {"target_node": "close_failed", "description": "three wrong answers", "condition": cond_vals("verify_identity_response.data.wrong_answers_left", ["0"])},
        tt("close_declined", "The customer refused the security questions (or asked what the call is about instead of answering) a second time, after the explanation"),
        tt("transfer_human", "The customer insists on speaking to a human after being asked once whether you can help"),
    ],
}

nodes["fetch_account"] = {
    "name": "fetch_account", "node_type": "api_request", "passthrough": True, "passthrough_default_transition": "facts",
    "on_enter": {"type": "silent"}, "disable_inactivity_monitor": True,
    "pre_actions": [api_tool("get_account", "Get the late products.", {}, [], retries=2)],
    "response_mapping": [
        {"path": "data.products", "variable": "acct_products"},
        {"path": "data.products.0.product_id", "variable": "acct_product_id"},
        {"path": "data.products.0.type", "variable": "acct_type"},
        {"path": "data.products.0.card_last4", "variable": "acct_card_last4"},
        {"path": "data.products.0.past_due", "variable": "acct_past_due"},
        {"path": "data.products.0.days_late", "variable": "acct_days_late"},
        {"path": "data.products.0.minimum_payment", "variable": "acct_minimum_payment"},
        {"path": "data.products.0.instalment_amount", "variable": "acct_instalment_amount"},
    ],
    "suppress_tool_results": True,
    "transitions": [{"target_node": "facts", "description": "always", "condition": {"operator": "always"}}],
}

nodes["facts"] = code_node("facts", CALC, "negotiate", [
    {"target_node": "close_account_error", "description": "account data missing", "condition": cond_vals("acct_ok", ["no"])}])

nodes["negotiate"] = {
    "name": "negotiate", "node_type": "conversation",
    "on_enter": {"type": "tts", "text": NEGOTIATE_ENTRY},
    "on_enter_first_entry_only": True,
    "task_message": T_NEGOTIATE,
    "tools": [
        api_tool("create_promise", "Record ONE product's payment promise. CALL it, once per agreed product, only after the customer clearly said yes to your single read-back of all promises.",
                 {"product_id": {"type": "string", "description": "product_id copied exactly from the account facts", "default": "{{promise_product_id}}"},
                  "amount": {"type": "number", "description": "The agreed amount for this product, a number such as 126.00", "default": "{{promise_amount}}"},
                  "date": {"type": "string", "description": "The agreed date for this product, YYYY-MM-DD, one of its valid promise dates", "default": "{{promise_date}}"}},
                 ["product_id", "amount", "date"], post=PROMISE_POST, pre=PROMISE_PRE),
        api_tool("add_note", "Record why ONE product ended without a promise. CALL it for every product without a promise, only at the end when every product is settled and every confirmed promise is recorded. Never call it on a transfer.",
                 {"product_id": {"type": "string", "description": "product_id copied exactly from the account facts", "default": "{{note_product_id}}"},
                  "reason": {"type": "string", "enum": ["refused", "hardship", "dispute", "paid", "service_error"], "description": "One of: refused, hardship, dispute, paid, service_error.", "default": "{{note_reason}}"}},
                 ["product_id", "reason"], post=NOTE_POST, pre=NOTE_PRE),
    ],
    "max_node_turns": 40, "max_turn_transition": "finish",
    "transitions": [
        {"target_node": "finish", "description": "every product has a promise or a note", "condition": cond_vals("b_all_done", ["yes"])},
        tt("finish", "Every product now has a recorded promise or a saved note (call only after the last create_promise or add_note succeeded)"),
        tt("transfer_restructuring", "The customer asks for a new payment plan or lower instalments"),
        tt("transfer_support", "The customer in hardship accepted to be connected with the customer support team"),
        tt("transfer_human", "The customer insists on speaking to a human after being asked once whether you can help"),
    ],
}

nodes["finish"] = code_node("finish", FINISH, "goodbye")

for q, reason in (("restructuring", "restructuring"), ("support", "support"), ("general", "human")):
    name = "transfer_human" if q == "general" else f"transfer_{q}"
    nodes[name] = {
        "name": name, "node_type": "api_request", "passthrough": True, "passthrough_default_transition": f"close_{name}",
        "on_enter": {"type": "silent"}, "disable_inactivity_monitor": True,
        "pre_actions": [api_tool("transfer_to_agent", "Hand the call to a person.", {"queue": {"type": "string", "default": q}}, ["queue"], retries=1)],
        "transitions": [{"target_node": f"close_{name}", "description": "always", "condition": {"operator": "always"}}],
    }

BYE = " Thank you for your time, WHO. Goodbye."
# EDIT: outcome table and goodbye texts from the brief
CLOSES = {
    "close_promise": ("promise", "none", "Your promise is recorded. You will receive a text message confirming this promise." + BYE),
    "close_service_error": ("no_promise", "service_error", "I am sorry, I could not record your promise because of a technical problem." + BYE),
    "close_account_error": ("no_promise", "service_error", "I am sorry, I cannot access your account information right now because of a technical problem." + BYE),
    "close_refused": ("no_promise", "refused", "I understand." + BYE),
    "close_hardship": ("no_promise", "hardship", "I understand." + BYE),
    "close_paid": ("paid", "none", "Thank you. Payments can take one business day to appear, so I will not take a promise today." + BYE),
    "close_dispute": ("no_promise", "dispute", "I understand. I will not take a promise today." + BYE),
    "close_transfer_restructuring": ("transferred", "restructuring", "I am transferring you now to our restructuring team." + BYE),
    "close_transfer_support": ("transferred", "support", "I am transferring you now to our customer support team." + BYE),
    "close_transfer_human": ("transferred", "human", "I am transferring you now to a colleague." + BYE),
    "close_failed": ("not_verified", "failed", "I am sorry, I could not verify your identity, so I cannot continue. Please use the Freya Bank mobile app or visit a branch." + BYE),
    "close_declined": ("not_verified", "declined", "I understand." + BYE.replace(", WHO", "")),
    "close_callback": ("not_reached", "callback", "Thank you, we will call back then. Goodbye."),
    "close_no_callback": ("not_reached", "no_callback", "Thank you, we will try again another time. Goodbye."),
    "close_wrong_number": ("not_reached", "wrong_number", "I apologise for the inconvenience. Thank you, goodbye."),
    "close_deceased": ("not_reached", "deceased", "I am sorry for your loss. Thank you, goodbye."),
    "close_unclear": ("not_reached", "unclear", "I am sorry, I could not confirm who I am speaking with. Thank you, goodbye."),
}
for name, (o, r, f) in CLOSES.items():
    nodes[name] = code_node(name, close_code(o, r, f), "goodbye")

nodes["goodbye"] = {
    "name": "goodbye", "node_type": "end_call", "on_enter": {"type": "silent"}, "disable_inactivity_monitor": True,
    "transitions": [],
    "terminal": {"type": "function", "function": {"name": "call_closed", "handler": "__function__:end_call",
                 "parameters": {"type": "object", "properties": {
                     "message_mode": {"type": "string", "default": "custom"},
                     "spoken_message": {"type": "string", "default": "{{farewell}}"}}}}},
}

close_tool = api_tool(
    "close_call", "Record how the call ended.",
    {"outcome": {"type": "string", "enum": ["promise", "paid", "transferred", "no_promise", "not_verified", "not_reached"], "default": "{{close_outcome}}",
                 "description": "promise: a promise was recorded. paid: customer already paid. transferred: handed to a person. no_promise: verified, no promise. not_verified: identity check did not pass. not_reached: the customer was not on the line."},
     "reason": {"type": "string", "default": "{{close_reason}}",
                "description": "none for promise and paid. transferred: restructuring, human, support. no_promise: refused, hardship, dispute, service_error. not_verified: failed, declined. not_reached: callback, no_callback, wrong_number, deceased, unclear."}},
    ["outcome"], pre=CLOSE_PRE)
close_tool["config"]["alwaysRunsAt"] = ["end_of_call"]

wf = {
    "initial_node": "setup",
    "system_prompt": SYSTEM,
    "nodes": nodes,
    "global_nodes": {},
    "tools": [close_tool],
    "test_variables": {"first_name": "Daniel", "last_name": "Reyes", "title": "Mr", "now": "2026-10-14T11:00"},
}
# fix: third-party prompt uses a short form of the next business day
T = json.dumps(wf)
T = T.replace("{{callback_facts_short}}", "the next business day")
slot = sys.argv[1] if len(sys.argv) > 1 else "example"
out = pathlib.Path(__file__).resolve().parent / (slot + ".workflow.json")
out.write_text(json.dumps(json.loads(T), indent=1))
print("wrote", out, len(nodes), "nodes")
