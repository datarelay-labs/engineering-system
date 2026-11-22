#!/usr/bin/env python3
"""Historical mistake reproduction: source edits during a frozen E2E TEST pass."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import pwd
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TOOL = Path(__file__).with_name('agent_phase_guard.py')
spec = importlib.util.spec_from_file_location('agent_phase_guard', TOOL)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


def git(repo, *argv):
    result = subprocess.run(['git', '-C', str(repo), *argv], capture_output=True, text=True)
    if result.returncode:
        raise AssertionError(result.stderr)
    return result.stdout.strip()


class PhaseGuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='phase-guard-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = self.root / 'repo'
        self.repo.mkdir()
        git(self.repo, 'init', '-q')
        git(self.repo, 'config', 'user.email', 'phase-test@example.invalid')
        git(self.repo, 'config', 'user.name', 'Phase Test')
        (self.repo / 'docs').mkdir()
        (self.repo / 'docs' / 'E2E.md').write_text('TEST -> FREEZE -> FIX -> VERIFY\n')
        (self.repo / 'README.md').write_text('baseline\n')
        git(self.repo, 'add', '.')
        git(self.repo, 'commit', '-qm', 'baseline')
        self.head = git(self.repo, 'rev-parse', 'HEAD')
        self.contract = self.repo / 'docs' / 'E2E.md'
        self.ledger = self.root / 'frozen-ledger.json'
        self.ledger.write_text('{"result":"FINAL","scenarios":116}\n')
        self.plan = {
            'contract_version': 1,
            'repository': 'datarelay-labs/datarelay-link',
            'worktree': str(self.repo),
            'phase': 'TEST',
            'owner_phase': 'TEST',
            'pinned_head': self.head,
            'source_contract': 'docs/E2E.md',
            'source_contract_sha256': hashlib.sha256(self.contract.read_bytes()).hexdigest(),
            'owner_intent': {
                'goal': 'Complete all E2E scenarios before fixing issues',
                'problem': 'The assistant changes code during active tests',
            },
            'agent_restatement': {
                'goal': 'Test all applicable scenarios at a pinned HEAD and record failures',
                'problem': 'Prevent fixes and candidate rebuilds during TEST',
                'phase': 'TEST',
                'next_phase': 'FIX',
            },
        }

    def decide(self, effect):
        return module.evaluate(self.plan, effect)

    def test_red_reproduce_historical_midtest_fix_and_build_blocked(self):
        for effect in ('source_write', 'test_contract_write', 'git_commit', 'git_push', 'candidate_build'):
            with self.subTest(effect=effect):
                self.assertEqual(self.decide(effect)['status'], 'BLOCK')

    def test_test_allows_real_test_and_external_evidence(self):
        self.assertEqual(self.decide('test_run')['status'], 'ALLOW')
        self.assertEqual(self.decide('evidence_write')['status'], 'ALLOW')

    def test_head_drift_is_not_silently_promoted(self):
        (self.repo / 'README.md').write_text('updated\n')
        git(self.repo, 'add', 'README.md')
        git(self.repo, 'commit', '-qm', 'changed during test')
        result = self.decide('test_run')
        self.assertEqual(result['reason'], 'HEAD_DRIFT')

    def test_dirty_tree_is_detected(self):
        (self.repo / 'README.md').write_text('dirty\n')
        self.assertEqual(self.decide('test_run')['reason'], 'SOURCE_DIRTY')

    def test_contract_edit_is_detected(self):
        self.contract.write_text('Changed contract during TEST\n')
        self.assertIn(self.decide('test_run')['reason'], ('SOURCE_DIRTY', 'CONTRACT_DRIFT'))

    def test_agent_phase_disagreement_is_blocked(self):
        self.plan['agent_restatement']['phase'] = 'FIX'
        self.assertEqual(self.decide('test_run')['reason'], 'INTENT_PHASE_MISMATCH')

    def test_missing_restatement_fails_closed(self):
        self.plan['agent_restatement'].pop('problem')
        self.assertEqual(self.decide('test_run')['reason'], 'INTENT_RESTATEMENT_MISSING')

    def test_fix_requires_frozen_evidence(self):
        self.plan['phase'] = self.plan['owner_phase'] = self.plan['agent_restatement']['phase'] = 'FIX'
        self.assertEqual(self.decide('source_write')['reason'], 'FROZEN_EVIDENCE_REQUIRED')
        self.plan['frozen_evidence'] = {
            'path': str(self.ledger),
            'sha256': hashlib.sha256(self.ledger.read_bytes()).hexdigest(),
        }
        self.assertEqual(self.decide('source_write')['status'], 'ALLOW')
        self.ledger.write_text('tampered')
        self.assertEqual(self.decide('source_write')['reason'], 'FROZEN_EVIDENCE_MISMATCH')

    def test_agent_instruction_templates_preserve_test_then_fix(self):
        root = TOOL.parents[1]
        for relative in ('templates/CHATGPT_PROJECT_INSTRUCTION.txt', 'templates/CHATGPT_CUSTOM_INSTRUCTION.txt'):
            with self.subTest(relative=relative):
                content = (root / relative).read_text()
                self.assertIn('TEST/FIX/VERIFY', content)
                self.assertIn('do not edit source/docs/test contracts, rebuild or commit', content)
                self.assertIn('This project-specific phase rule overrides the general reproduce/fix loop', content)

    def test_replay_exposes_goal_and_limits_without_claiming_semantic_proof(self):
        summary = module.replay(self.plan)
        self.assertEqual(summary['status'], 'ALLOW')
        self.assertEqual(summary['agent_restatement']['phase'], 'TEST')
        self.assertEqual(summary['semantic_equivalence'], 'NOT_MACHINE_VERIFIED')
        self.assertIn('git_commit', summary['denied_effects'])

    def test_cli_check_refuses_midtest_commit_with_exit_two(self):
        plan_file = self.root / 'plan.json'
        plan_file.write_text(json.dumps(self.plan))
        result = subprocess.run([sys.executable, str(TOOL), 'check', '--plan', str(plan_file), '--effect', 'git_commit'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stdout)['reason'], 'EFFECT_FORBIDDEN_IN_PHASE')

    def test_bad_restatement_shape_fails_closed(self):
        self.plan['agent_restatement'] = 'yes'
        self.assertEqual(self.decide('read')['status'], 'BLOCK')

    def test_verify_disallows_mutations(self):
        self.plan['phase'] = self.plan['owner_phase'] = self.plan['agent_restatement']['phase'] = 'VERIFY'
        self.assertEqual(self.decide('git_commit')['status'], 'BLOCK')

    def test_authority_sensitive_effects_never_self_authorize(self):
        self.plan['phase'] = self.plan['owner_phase'] = self.plan['agent_restatement']['phase'] = 'FIX'
        self.plan['frozen_evidence'] = {
            'path': str(self.ledger),
            'sha256': hashlib.sha256(self.ledger.read_bytes()).hexdigest(),
        }
        for effect in ('production_write', 'permissions_change', 'merge', 'release', 'shared_destructive'):
            self.assertEqual(self.decide(effect)['status'], 'BLOCK')

    @unittest.skipUnless(os.geteuid() == 0 and any(p.pw_name == 'nobody' for p in pwd.getpwall()), 'OS privilege boundary needs root and existing nobody')
    def test_external_gitdir_in_linked_worktree_is_rejected(self):
        acct = pwd.getpwnam('nobody')
        moved = self.root / 'git-metadata-outside-source'
        (self.repo / '.git').rename(moved)
        (self.repo / '.git').write_text('gitdir: ' + str(moved) + '\n')
        with self.assertRaisesRegex(module.GuardError, 'STANDALONE_READ_ONLY_CHECKOUT_REQUIRED'):
            module._assert_source_read_only(self.repo, acct.pw_uid, acct.pw_gid, {acct.pw_gid})

    @unittest.skipUnless(os.geteuid() == 0 and any(p.pw_name == 'nobody' for p in pwd.getpwall()), 'OS privilege boundary needs root and existing nobody')
    def test_writable_parent_of_checkout_is_rejected(self):
        acct = pwd.getpwnam('nobody')
        os.chown(self.root, acct.pw_uid, acct.pw_gid)
        os.chmod(self.root, 0o700)
        with self.assertRaisesRegex(module.GuardError, 'SOURCE_PARENT_WRITABLE_BY_TEST_ACTOR'):
            module._assert_source_read_only(self.repo, acct.pw_uid, acct.pw_gid, {acct.pw_gid})

    @unittest.skipUnless(os.geteuid() == 0 and any(p.pw_name == 'nobody' for p in pwd.getpwall()), 'OS privilege boundary needs root and existing nobody')
    def test_test_actor_owned_read_only_file_is_rejected(self):
        acct = pwd.getpwnam('nobody')
        file = self.repo / 'README.md'
        os.chown(file, acct.pw_uid, acct.pw_gid)
        os.chmod(file, 0o400)
        out = self.root / 'evidence'
        out.mkdir()
        os.chown(out, acct.pw_uid, acct.pw_gid)
        with self.assertRaisesRegex(module.GuardError, 'SOURCE_OWNED_BY_TEST_ACTOR'):
            module.run_isolated_test(self.plan, [sys.executable, '-B', '-c', 'print(1)'], user='nobody', evidence_dir=out)

    @unittest.skipUnless(os.geteuid() == 0 and any(p.pw_name == 'nobody' for p in pwd.getpwall()), 'OS privilege boundary needs root and existing nobody')
    def test_real_os_isolation_blocks_midtest_write_but_allows_evidence(self):
        # All fixture files were created by root. A real less-privileged actor may read
        # this tree but cannot chmod or edit it; evidence is outside and writable.
        os.chmod(self.root, 0o755)
        os.chmod(self.repo, 0o755)
        out = self.root / 'evidence'
        out.mkdir()
        nobody = pwd.getpwnam('nobody')
        os.chown(out, nobody.pw_uid, nobody.pw_gid)
        os.chmod(out, 0o700)
        command = [sys.executable, '-B', '-c',
            'from pathlib import Path; Path("README.md").write_text("WRONG")']
        blocked = module.run_isolated_test(self.plan, command, user='nobody', evidence_dir=out)
        self.assertNotEqual(blocked['exit_code'], 0)
        self.assertIn('PermissionError', blocked['stdout_tail'])
        self.assertEqual((self.repo / 'README.md').read_text(), 'baseline\n')
        self.assertEqual(git(self.repo, 'rev-parse', 'HEAD'), self.head)
        ok = module.run_isolated_test(self.plan, [sys.executable, '-B', '-c',
            'from pathlib import Path; Path("' + str(out / 'receipt.txt') + '").write_text("PASS")'], user='nobody', evidence_dir=out)
        self.assertEqual(ok['exit_code'], 0)
        self.assertEqual((out / 'receipt.txt').read_text(), 'PASS')


if __name__ == '__main__':
    unittest.main(verbosity=2)
