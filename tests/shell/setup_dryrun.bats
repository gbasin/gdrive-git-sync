#!/usr/bin/env bats
# Integration tests for setup.sh (dry-run / help / bad flags)

setup() {
  load test_helper/bats-support/load
  load test_helper/bats-assert/load
  ROOT_DIR="${BATS_TEST_DIRNAME}/../.."
}

@test "setup.sh --help prints usage and exits 0" {
  run "$ROOT_DIR/scripts/setup.sh" --help
  assert_success
  assert_output --partial "Usage:"
  assert_output --partial "--non-interactive"
  assert_output --partial "--dry-run"
}

@test "setup.sh rejects unknown flags" {
  run "$ROOT_DIR/scripts/setup.sh" --bogus
  assert_failure
  assert_output --partial "Unknown flag: --bogus"
}

@test "setup.sh --dry-run completes successfully" {
  run "$ROOT_DIR/scripts/setup.sh" --dry-run
  assert_success
  assert_output --partial "Setup complete!"
}
