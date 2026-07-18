"""Test script to verify Supabase connection.

Run: python scripts/test_supabase.py
Expected output: "Supabase connection successful."
"""

import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.supabase_service import get_supabase


def main() -> None:
    try:
        client = get_supabase()

        response = (
            client
            .table("research_jobs")
            .select("id")
            .limit(1)
            .execute()
        )

        print("Supabase connection successful.")
        print(f"Data: {response.data}")

    except Exception as e:
        print(f"Supabase connection failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
