"""Local-only credential provisioning. Never commit .local-data to GitHub."""
from pathlib import Path
import getpass
import hashlib
import json
import os
import secrets
from app import PRIVATE, USERS, SHIFT_RULES


def main():
    PRIVATE.mkdir(parents=True, exist_ok=True)
    accounts = json.loads(USERS.read_text()) if USERS.exists() else {}
    print('IND3 V3.1 local access setup (credentials stay on this computer)')
    print('Enter each authorized associate number, including management identities.')
    while True:
        assoc = input('Associate number (blank to finish): ').strip()
        if not assoc:
            break
        if not assoc.isdecimal() or len(assoc) > 12:
            print('Numbers only, up to 12 digits.'); continue
        name = input('Display name: ').strip() or 'Associate'
        all_shifts = input('Full S1–S5 access? [y/N]: ').lower().strip() == 'y'
        shift = None if all_shifts else input('Assigned shift (S1–S5): ').strip().upper()
        if not all_shifts and shift not in SHIFT_RULES:
            print('Invalid assigned shift.');continue
        pw = getpass.getpass('Access passphrase (12+ characters): ')
        confirm = getpass.getpass('Repeat passphrase: ')
        if pw != confirm or len(pw) < 12:
            print('Passphrase mismatch or under 12 characters.'); continue
        salt = secrets.token_bytes(16)
        digest = hashlib.scrypt(pw.encode(),salt=salt,n=2**14,r=8,p=1,dklen=32)
        accounts[assoc] = {'name':name,'all_shifts':all_shifts,'shift':shift,
                           'salt':salt.hex(),'hash':digest.hex()}
        print('Saved account configuration for this computer.')
    if accounts:
        fd = os.open(USERS, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(accounts, f, indent=2)
        if os.name != 'nt':
            USERS.chmod(0o600)
        print(f'Configured {len(accounts)} account(s).')
    else:
        print('No accounts configured.')

if __name__ == '__main__':
    main()