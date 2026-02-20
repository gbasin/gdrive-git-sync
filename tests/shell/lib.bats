#!/usr/bin/env bats
# Unit tests for scripts/lib.sh

setup() {
  load test_helper/bats-support/load
  load test_helper/bats-assert/load
  # shellcheck source=../../scripts/lib.sh
  source "${BATS_TEST_DIRNAME}/../../scripts/lib.sh"
}

# --- brew_pkg ---

@test "brew_pkg: gcloud returns cask" {
  run brew_pkg gcloud
  assert_output "--cask google-cloud-sdk"
}

@test "brew_pkg: terraform returns tap formula" {
  run brew_pkg terraform
  assert_output "hashicorp/tap/terraform"
}

@test "brew_pkg: git returns git" {
  run brew_pkg git
  assert_output "git"
}

@test "brew_pkg: unknown tool returns empty" {
  run brew_pkg unknown
  assert_output ""
}

# --- apt_pkg ---

@test "apt_pkg: git returns git" {
  run apt_pkg git
  assert_output "git"
}

@test "apt_pkg: zip returns zip" {
  run apt_pkg zip
  assert_output "zip"
}

@test "apt_pkg: gcloud returns empty (no apt package)" {
  run apt_pkg gcloud
  assert_output ""
}

@test "apt_pkg: terraform returns empty" {
  run apt_pkg terraform
  assert_output ""
}

@test "apt_pkg: gh returns empty" {
  run apt_pkg gh
  assert_output ""
}

# --- install_url ---

@test "install_url: gcloud returns cloud.google.com" {
  run install_url gcloud
  assert_output --partial "cloud.google.com"
}

@test "install_url: gh returns cli.github.com" {
  run install_url gh
  assert_output "https://cli.github.com"
}

# --- extract_drive_folder_id ---

@test "extract_drive_folder_id: full URL" {
  run extract_drive_folder_id "https://drive.google.com/drive/folders/1aBcDeFgHiJkLmNoPqRsTuVwXyZ"
  assert_success
  assert_output "1aBcDeFgHiJkLmNoPqRsTuVwXyZ"
}

@test "extract_drive_folder_id: URL with query params" {
  run extract_drive_folder_id "https://drive.google.com/drive/folders/1aBcDeFgHiJk?resourcekey=abc"
  assert_success
  assert_output "1aBcDeFgHiJk"
}

@test "extract_drive_folder_id: my-drive URL rejected" {
  run extract_drive_folder_id "https://drive.google.com/drive/my-drive"
  assert_failure
}

@test "extract_drive_folder_id: root keyword rejected" {
  run extract_drive_folder_id "root"
  assert_failure
}

@test "extract_drive_folder_id: bare folder ID" {
  run extract_drive_folder_id "1aBcDeFgHiJkLmNoPqRsTuVwXyZ"
  assert_success
  assert_output "1aBcDeFgHiJkLmNoPqRsTuVwXyZ"
}

@test "extract_drive_folder_id: multi-account URL (u/0/folders)" {
  run extract_drive_folder_id "https://drive.google.com/drive/u/0/folders/1aBcDeFgHiJkLmNo"
  assert_success
  assert_output "1aBcDeFgHiJkLmNo"
}

@test "extract_drive_folder_id: URL with trailing slash" {
  run extract_drive_folder_id "https://drive.google.com/drive/folders/1aBcDeFgHiJkLmNo/"
  assert_success
  assert_output "1aBcDeFgHiJkLmNo"
}

@test "extract_drive_folder_id: short string fails" {
  run extract_drive_folder_id "abc"
  assert_failure
}

@test "extract_drive_folder_id: garbage input fails" {
  run extract_drive_folder_id "not a url at all!"
  assert_failure
}

# --- validate_gcp_project_id ---

@test "validate_gcp_project_id: valid simple ID" {
  run validate_gcp_project_id "my-project-123456"
  assert_success
}

@test "validate_gcp_project_id: valid minimal (6 chars)" {
  run validate_gcp_project_id "abcdef"
  assert_success
}

@test "validate_gcp_project_id: valid 30 chars" {
  run validate_gcp_project_id "abcdefghijklmnopqrstuvwxyz1234"
  assert_success
}

@test "validate_gcp_project_id: rejects uppercase" {
  run validate_gcp_project_id "My-Project"
  assert_failure
}

@test "validate_gcp_project_id: rejects starting with digit" {
  run validate_gcp_project_id "1project"
  assert_failure
}

@test "validate_gcp_project_id: rejects too short (5 chars)" {
  run validate_gcp_project_id "abcde"
  assert_failure
}

@test "validate_gcp_project_id: rejects ending with hyphen" {
  run validate_gcp_project_id "my-project-"
  assert_failure
}

@test "validate_gcp_project_id: rejects empty" {
  run validate_gcp_project_id ""
  assert_failure
}

# --- detect_git_host ---

@test "detect_git_host: github.com" {
  run detect_git_host "https://github.com/user/repo.git"
  assert_output "github"
}

@test "detect_git_host: gitlab.com" {
  run detect_git_host "https://gitlab.com/group/repo.git"
  assert_output "gitlab"
}

@test "detect_git_host: bitbucket.org" {
  run detect_git_host "https://bitbucket.org/user/repo.git"
  assert_output "bitbucket"
}

@test "detect_git_host: self-hosted returns other" {
  run detect_git_host "https://git.example.com/user/repo.git"
  assert_output "other"
}

# --- extract_github_owner_repo ---

@test "extract_github_owner_repo: HTTPS with .git" {
  run extract_github_owner_repo "https://github.com/myuser/myrepo.git"
  assert_success
  assert_output "myuser myrepo"
}

@test "extract_github_owner_repo: HTTPS without .git" {
  run extract_github_owner_repo "https://github.com/myuser/myrepo"
  assert_success
  assert_output "myuser myrepo"
}

@test "extract_github_owner_repo: SSH URL" {
  run extract_github_owner_repo "git@github.com:myuser/myrepo.git"
  assert_success
  assert_output "myuser myrepo"
}

@test "extract_github_owner_repo: non-github URL fails" {
  run extract_github_owner_repo "https://gitlab.com/user/repo.git"
  assert_failure
}
