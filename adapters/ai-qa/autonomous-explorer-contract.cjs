'use strict';
/**
 * Pure contract for bounded model-selected browser exploration.
 * Untrusted page text, selector content and model action JSON confer no authority.
 */
const crypto = require('node:crypto');

const SHA = /^[a-f0-9]{64}$/;
const IDENT = /^[a-zA-Z][a-zA-Z0-9._-]{0,63}$/;
const ELEMENT = /^e[0-9]{1,3}$/;
const METHODS = new Set(['GET', 'HEAD', 'OPTIONS']);
const KINDS = new Set(['click', 'fill', 'press', 'scroll', 'back', 'select']);
const SAFE_KEYS = new Set(['Tab', 'Escape', 'Enter', 'ArrowDown', 'ArrowUp']);
const DENY_ACTION_TEXT = /\b(approve|authorize|submit payment|purchase|buy now|checkout|delete|remove|revoke|reset|factory|deploy|publish|release|send email|send data|transfer|sign in|log in|logout|sign out|disable|enable|install|uninstall|update|upgrade)\b/i;
const DENY_API_PATH = /\/(auth|login|logout|token|password|approve|decision|send|upload|import|delete|remove|revoke|deploy|install|publish|release|execute|run-now|reset|factory|credential|secret)(?:\/|$|[-_])/i;

class ContractError extends Error {
  constructor(code) { super(code); this.code = code; }
}
function ensure(value, code) { if (!value) throw new ContractError(code); }
function plain(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    && Object.getPrototypeOf(value) === Object.prototype;
}
function exact(value, fields, code) {
  ensure(plain(value) && Object.keys(value).every(k => fields.includes(k))
    && fields.every(k => Object.hasOwn(value, k)), code);
}
function sha256(buffer) { return crypto.createHash('sha256').update(buffer).digest('hex'); }
function safeOrigin(url) {
  ensure(url instanceof URL && !url.username && !url.password && !url.hash,
    'UNSAFE_TARGET_URL');
  const local = url.protocol === 'http:' &&
    (url.hostname === '127.0.0.1' || url.hostname === 'localhost');
  const development = url.protocol === 'https:' && url.hostname === 'app.parser.xdr.ooo';
  ensure(local || development, 'TARGET_NOT_ALLOWLISTED');
  ensure(url.pathname === '/' && !url.search, 'TARGET_NOT_ROOT');
  return url.origin;
}
function validateMission(raw, expectedDigest) {
  ensure(Buffer.isBuffer(raw) && raw.byteLength <= 16 * 1024 && raw.byteLength > 0,
    'UNBOUNDED_MISSION');
  ensure(typeof expectedDigest === 'string' && SHA.test(expectedDigest)
    && sha256(raw) === expectedDigest, 'UNPINNED_REQUIREMENTS');
  let m;
  try { m = JSON.parse(raw.toString('utf8')); }
  catch (_) { throw new ContractError('INVALID_MISSION_JSON'); }
  exact(m, ['schema_version', 'kind', 'classification', 'goal', 'target',
    'max_actions', 'allowed_post_paths', 'expected_outcomes'], 'BAD_MISSION_SCHEMA');
  ensure(m.schema_version === 1 && m.kind === 'independent-ai-browser-mission'
    && m.classification === 'SYNTHETIC_PUBLIC_DEVELOPMENT_ONLY', 'MISSION_KIND');
  ensure(typeof m.goal === 'string' && m.goal.length >= 30 && m.goal.length <= 1200,
    'INVALID_GOAL');
  ensure(typeof m.target === 'string' && m.target.length <= 180, 'INVALID_TARGET');
  let url;
  try { url = new URL(m.target); }
  catch (_) { throw new ContractError('INVALID_TARGET'); }
  const origin = safeOrigin(url);
  ensure(Number.isInteger(m.max_actions) && m.max_actions >= 3 && m.max_actions <= 12,
    'INVALID_ACTION_BUDGET');
  ensure(Array.isArray(m.allowed_post_paths) && m.allowed_post_paths.length <= 3
    && new Set(m.allowed_post_paths).size === m.allowed_post_paths.length,
    'INVALID_POST_PATHS');
  for (const p of m.allowed_post_paths) {
    ensure(typeof p === 'string' && /^\/api\/v[0-9]+\/[a-z0-9/_-]+$/.test(p)
      && !DENY_API_PATH.test(p), 'UNSAFE_POST_PATH');
  }
  // Real public dev site is restricted to its known, read-only-computation preview API.
  if (url.hostname === 'app.parser.xdr.ooo') {
    ensure(m.allowed_post_paths.every(p => p === '/api/v1/multipass/preview-batch'),
      'PUBLIC_POST_NOT_ALLOWED');
  }
  ensure(Array.isArray(m.expected_outcomes) && m.expected_outcomes.length >= 1
    && m.expected_outcomes.length <= 10, 'MISSING_ORACLE');
  const ids = new Set();
  for (const item of m.expected_outcomes) {
    exact(item, ['id', 'expected'], 'INVALID_ORACLE');
    ensure(typeof item.id === 'string' && IDENT.test(item.id) && !ids.has(item.id),
      'DUPLICATE_ORACLE_ID');
    ids.add(item.id);
    ensure(typeof item.expected === 'string' && item.expected.length >= 12
      && item.expected.length <= 500, 'INVALID_ORACLE_TEXT');
  }
  return { ...m, origin, expected_plan_sha256: expectedDigest };
}
function allowRequest(mission, rawURL, method) {
  if (typeof method !== 'string') return { allowed: false, reason: 'INVALID_METHOD' };
  try {
    const u = new URL(rawURL);
    if (u.origin !== mission.origin || u.username || u.password)
      return {allowed:false,reason:'CROSS_ORIGIN'};
    if (METHODS.has(method)) return {allowed:true};
    if (method === 'POST' && mission.allowed_post_paths.includes(u.pathname) && !u.search)
      return {allowed:true};
    return {allowed:false,reason:'MUTATION_NOT_ALLOWED'};
  } catch (_) {
    return {allowed:false,reason:'MALFORMED_REQUEST'};
  }
}
function approveAction(command, observation, budgetUsed, mission) {
  exact(command, ['cmd', 'observation_id', 'action', 'reason'], 'INVALID_ACTION_OBJECT');
  ensure(command.cmd === 'act' && observation && typeof observation.id === 'string'
    && command.observation_id === observation.id, 'STALE_OBSERVATION');
  ensure(typeof command.reason === 'string' && command.reason.length >= 10
    && command.reason.length <= 600, 'MISSING_AI_DECISION_REASON');
  ensure(Number.isInteger(budgetUsed) && budgetUsed < mission.max_actions,
    'ACTION_BUDGET_EXCEEDED');
  const a = command.action;
  ensure(plain(a) && KINDS.has(a.kind), 'UNKNOWN_ACTION_KIND');
  const required = {
    click: ['kind', 'ref'],
    fill: ['kind', 'ref', 'text'],
    press: ['kind', 'ref', 'key'],
    select: ['kind', 'ref', 'value'],
    scroll: ['kind', 'direction'],
    back: ['kind'],
  }[a.kind];
  exact(a, required, 'INVALID_ACTION_FIELDS');
  if (a.kind === 'scroll') {
    ensure(['up', 'down'].includes(a.direction), 'INVALID_SCROLL_DIRECTION');
    return {action: a, target: null};
  }
  if (a.kind === 'back') return {action: a, target:null};
  ensure(typeof a.ref === 'string' && ELEMENT.test(a.ref), 'INVALID_ELEMENT_REF');
  const target = observation.elements.find(el => el.ref === a.ref);
  ensure(target, 'UNKNOWN_OR_STALE_ELEMENT_REF');
  ensure(target.enabled && target.visible, 'UNAVAILABLE_ELEMENT');
  if (a.kind === 'click') {
    ensure(!DENY_ACTION_TEXT.test(target.name), 'SENSITIVE_ACTION_DENIED');
  }
  if (a.kind === 'fill') {
    ensure(['input', 'textarea', 'contenteditable'].includes(target.kind)
      && !['password', 'hidden', 'email', 'tel', 'file'].includes(target.input_type)
      && !/\b(password|token|secret|credential|api.key|mfa|otp|pin|email|username)\b/i.test(target.name),
      'SENSITIVE_INPUT_DENIED');
    ensure(typeof a.text === 'string' && a.text.length > 0
      && Buffer.byteLength(a.text, 'utf8') <= 8192 && !a.text.includes('\u0000'),
      'UNSAFE_INPUT');
  }
  if (a.kind === 'press') {
    ensure(SAFE_KEYS.has(a.key), 'UNSAFE_KEY');
  }
  if (a.kind === 'select') {
    ensure(target.kind === 'select'
      && typeof a.value === 'string'
      && Array.isArray(target.options)
      && target.options.some(opt => opt.value === a.value && !opt.disabled)
      && !DENY_ACTION_TEXT.test(target.name), 'INVALID_SELECT_OPTION');
  }
  return {action:a, target};
}
function approveFinish(command, observation, actionCount) {
  exact(command, ['cmd','observation_id','disposition','rationale',
    'oracle_status','finding_ids'], 'INVALID_FINISH_OBJECT');
  ensure(command.cmd === 'finish' && observation && command.observation_id === observation.id,
    'STALE_OBSERVATION');
  ensure(actionCount >= 1, 'NO_MODEL_EXPLORATION');
  ensure(['NO_CONFIRMED_FINDING','POTENTIAL_FINDING','BLOCKED','UNVERIFIED'].includes(command.disposition),
    'UNAUTHORIZED_PASS_VERDICT');
  ensure(typeof command.rationale === 'string' && command.rationale.length >= 20
    && command.rationale.length <= 1200, 'MISSING_FINISH_RATIONALE');
  ensure(plain(command.oracle_status)
    && Object.keys(command.oracle_status).length > 0
    && Object.keys(command.oracle_status).every(key => IDENT.test(key)
      && ['OBSERVED','FAIL','UNVERIFIED','BLOCKED'].includes(command.oracle_status[key])),
    'INVALID_ORACLE_STATUSES');
  ensure(Array.isArray(command.finding_ids)
    && command.finding_ids.length <= 10
    && command.finding_ids.every(id => typeof id === 'string' && IDENT.test(id)),
    'INVALID_FINDINGS');
  ensure(new Set(command.finding_ids).size === command.finding_ids.length,
    'DUPLICATE_FINDINGS');
  if (command.disposition === 'NO_CONFIRMED_FINDING')
    ensure(!command.finding_ids.length
      && !Object.values(command.oracle_status).includes('FAIL'), 'CONTRADICTORY_CLEAR_RESULT');
  if (command.disposition === 'POTENTIAL_FINDING')
    ensure(command.finding_ids.length > 0, 'MISSING_FINDING_ID');
  return { ...command, authority:'T0_ADVISORY_ONLY', full_user_e2e_verified:false,
    releases_product:false };
}
module.exports={ContractError,sha256,validateMission,allowRequest,approveAction,
  approveFinish, safeOrigin,DENY_ACTION_TEXT};
