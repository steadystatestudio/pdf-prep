import sys
from pathlib import Path

if "--self-test" in sys.argv:
    from pdf_prep.smoke import main
    raise SystemExit(main(Path(sys.argv[sys.argv.index("--self-test") + 1])))

from pdf_prep.app import main
raise SystemExit(main())
