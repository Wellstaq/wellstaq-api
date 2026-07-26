"""
scripts/seed_all.py

Runs every seed script in the correct dependency order, in one command.
Each script is still fully independent and safe to run on its own if
needed — this is just a convenience runner, not a replacement for them.

Order matters:
  1. seed_permissions   — roles/permissions (org signup depends on these existing)
  2. seed_categories    — department/club/health category lookups
  3. seed_wellbeing     — wellbeing dimensions + challenge categories
  4. seed_subscription_plans — must run BEFORE seed_paystack_plan, since
     that script looks up existing SubscriptionPlan rows to attach a
     Paystack plan_code to.
  5. seed_paystack_plan — creates the matching Paystack Plan + writes
     external_plan_id back onto subscription_plans (needs network
     access to Paystack's API — will fail if PAYSTACK_SECRET_KEY isn't
     set, same as running it standalone).

seed_dev_db.py is intentionally NOT included here — that one rebuilds
the schema and seeds throwaway test data (org/users/etc.) for local
console testing, which is a different concern from these catalogue/
lookup seeds. Run it separately 
Usage:
    python -m scripts.seed_all
"""

from __future__ import annotations

import subprocess
import sys

SEED_SCRIPTS_IN_ORDER = [
    "scripts.seed_permissions",
    "scripts.seed_categories",
    "scripts.seed_wellbeing",
    "scripts.seed_subscription_plans",
    "scripts.seed_paystack_plan",
]


def run_seed(module_name: str) -> bool:
    print(f"\n{'=' * 60}\nRunning {module_name}\n{'=' * 60}")
    result = subprocess.run([sys.executable, "-m", module_name])
    if result.returncode != 0:
        print(f"✗ {module_name} failed (exit code {result.returncode}).")
        return False
    return True


def main() -> None:
    for module_name in SEED_SCRIPTS_IN_ORDER:
        success = run_seed(module_name)
        if not success:
            print(f"\nStopping — {module_name} failed. Fix the error above before continuing.")
            sys.exit(1)

    print(f"\n{'=' * 60}\n✓ All seed scripts completed successfully.\n{'=' * 60}")


if __name__ == "__main__":
    main()