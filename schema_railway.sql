-- ══════════════════════════════════════════════════════════════
--  Plant Open Issue Tracker — Database Schema (Railway version)
--  Run this while already connected to the correct database
--  (e.g. after "USE railway;" or via railway connect MySQL)
-- ══════════════════════════════════════════════════════════════

-- ── ISSUES ───────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS issues (
    id            INT AUTO_INCREMENT PRIMARY KEY,
    description   TEXT         NOT NULL,
    department    VARCHAR(100) NOT NULL,
    priority      VARCHAR(20)  NOT NULL,
    responsible   VARCHAR(100) NOT NULL,
    email         VARCHAR(150) NOT NULL,
    target_date   DATE,
    status        VARCHAR(30)  NOT NULL DEFAULT 'Open',
    progress      INT          NOT NULL DEFAULT 0,
    remark        TEXT,
    created_at    DATETIME     NOT NULL,
    last_updated  DATETIME     NOT NULL
);

-- ── ISSUE UPDATES (approved history) ────────────────────────
CREATE TABLE IF NOT EXISTS issue_updates (
    id               INT AUTO_INCREMENT PRIMARY KEY,
    issue_id         INT          NOT NULL,
    status           VARCHAR(30)  NOT NULL,
    progress         INT          NOT NULL DEFAULT 0,
    remark           TEXT,
    updated_by       VARCHAR(100) NOT NULL,
    approval_status  VARCHAR(20)  NOT NULL DEFAULT 'Approved',
    updated_at       DATETIME     NOT NULL,
    FOREIGN KEY (issue_id) REFERENCES issues(id) ON DELETE CASCADE
);

-- ── PENDING UPDATES (awaiting admin approval) ───────────────
CREATE TABLE IF NOT EXISTS pending_updates (
    id                  INT AUTO_INCREMENT PRIMARY KEY,
    issue_id            INT          NOT NULL,
    proposed_status     VARCHAR(30)  NOT NULL,
    proposed_progress   INT          NOT NULL DEFAULT 0,
    remark              TEXT,
    submitted_by        VARCHAR(100) NOT NULL,
    submitted_at        DATETIME     NOT NULL,
    decision            VARCHAR(20)  NOT NULL DEFAULT 'Pending',
    admin_remark        TEXT,
    decided_at          DATETIME,
    FOREIGN KEY (issue_id) REFERENCES issues(id) ON DELETE CASCADE
);
