# Experimental ngspice 46 model-bin index

The full SRAM parser was sampled in `INPgetModBin` / `model_name_match`:
every MOS lookup scanned the global model list. This patch indexes numeric
model bins by their exact prefix. Each prefix retains the original linked-list
order, including overlapping ranges. Device models, range tests, transistor
equations, stimulus and tolerances are unchanged.

The patch applies only to official **ngspice 46**, Git revision
`ebdaf58ec76a06ffaac7e0f138360dd1cf5ee4b6`. `build-20261004.json` records release,
original source, patch and executable hashes. This is an experimental host
simulator; the pinned OpenLane image and production macro Liberty are unchanged.

Download the release URL in that manifest and check its SHA256. Extract into a
new directory, check the eight original-file hashes, then apply:

```sh
patch -p1 < /absolute/path/to/ngspice-46.patch
mkdir ../build-indexed
cd ../build-indexed
../ngspice-46/configure --prefix=/absolute/temporary/install \
  --without-x --disable-xspice --enable-klu --disable-openmp \
  --with-readline=/opt/homebrew/opt/readline \
  CFLAGS='-O2 -I/opt/homebrew/opt/readline/include' \
  LDFLAGS='-L/opt/homebrew/opt/readline/lib'
make -j6
make install
make check
```

The readline paths above are for the measured macOS arm64 build; adapt them to
the host. Keep the installation in a separate directory. Explicit `.OPTIONS
KLU` in the deck selects KLU; compile-time support alone does not select it.

Compare against an unmodified ngspice 46:

```sh
python3 compare.py --original /path/to/original/ngspice \
  --indexed /path/to/indexed/ngspice --output-dir /new/comparison/directory
```

The synthetic comparisons preserve 15-digit printed currents across different
bins, overlapping-bin order, duplicate names and unrelated prefixes. The separate `compare_sky130.py` comparison checks actual SKY130 two-device
SS/TT/FF operating points against the original executable; invoke it with the
same binary/output arguments plus `--pdk-root /path/to/pdk`. These checks do
not establish whole-SRAM functional correctness, wider table coverage or PVT
qualification. Keep each real characterization's manifest and archived decks,
logs and measurements; a partial Liberty is not usable evidence.

Official source: <https://git.code.sf.net/p/ngspice/ngspice>.
The upstream discussion of SRAM model loading is at
<https://sourceforge.net/p/ngspice/mailman/ngspice-devel/thread/CA%2BzmTApB443QPK%2B2NHs7yAgtq6cQUoLx2aB3c-QrsfjuAhpb9w%40mail.gmail.com/>.
