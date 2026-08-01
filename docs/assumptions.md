# Assumptions register

> Status: authoritative figure register · Last updated 2026-08-01 ·
> Back to [planning](planning.md)

Two distinct things live here, kept firmly apart per the
[facts-vs-assumptions principle](design/domain-model.md):

1. **Verified policy figures** — facts of current UK law, verified against
   primary sources (gov.uk / HMRC manuals / legislation), each with source
   URL and retrieval date. These become the
   [region data files](design/uk-region.md) in Phase 2.
2. **Default assumptions** — estimates the app ships (returns, inflation,
   longevity, annuity rates). Every one is user-overridable and carries its
   basis. This section is the human-readable mirror of the future
   `regions/uk/data/assumptions_default.toml`; a doc-sync test (Phase 2)
   will keep the two aligned.

All verification below performed **2026-08-01** from live-fetched pages —
no figures from model training data.

---

## Part 1 — Verified policy figures (tax year 2026/27 unless stated)

### Income tax (rUK: England, Wales, NI)

| Figure | Value | Source |
| --- | --- | --- |
| Personal allowance | £12,570 | [gov.uk/income-tax-rates](https://www.gov.uk/income-tax-rates); [rates & thresholds for employers 2026–27](https://www.gov.uk/guidance/rates-and-thresholds-for-employers-2026-to-2027) |
| PA taper | −£1 per £2 of adjusted net income above £100,000; PA = £0 at £125,140 | [gov.uk/income-tax-rates](https://www.gov.uk/income-tax-rates) |
| Basic rate | 20% on taxable income £0–£37,700 above PA | both pages above (conventions cross-check: 12,570 + 37,700 = 50,270) |
| Higher rate | 40% on taxable income £37,701–£125,140 | same |
| Additional rate | 45% above £125,140 | same |
| Threshold freeze | PA and higher-rate threshold frozen to **5 April 2031** (Budget 2025 extended the freeze from 2028) | [Budget 2025 threshold-maintenance policy paper](https://www.gov.uk/government/publications/maintaining-income-tax-and-equivalent-national-insurance-contributions-thresholds-until-5-april-2031/income-tax-maintaining-the-personal-allowance-and-the-basic-rate-limit-for-income-tax-and-equivalent-national-insurance-contributions-thresholds-unt) |
| Starting rate for savings limit | £5,000 (maintained 2026/27–2030/31) | [Budget 2025 OOTLAR](https://www.gov.uk/government/publications/budget-2025-overview-of-tax-legislation-and-rates-ootlar/budget-2025-overview-of-tax-legislation-and-rates-ootlar) |
| Marriage allowance | £1,260 transferable (max saving £252/yr) | [gov.uk/marriage-allowance](https://www.gov.uk/marriage-allowance) — page current but not tax-year-stamped; arithmetically fixed while PA is frozen |

### Income tax (Scotland — designed-for, non-savings/non-dividend income)

| Band | Rate | Taxable income above PA | Source |
| --- | --- | --- | --- |
| Starter | 19% | £0–£3,967 | [gov.uk/scottish-income-tax](https://www.gov.uk/scottish-income-tax); [employers rates 2026–27](https://www.gov.uk/guidance/rates-and-thresholds-for-employers-2026-to-2027) |
| Basic | 20% | £3,968–£16,956 | same |
| Intermediate | 21% | £16,957–£31,092 | same |
| Higher | 42% | £31,093–£62,430 | same |
| Advanced | 45% | £62,431–£125,140 | same |
| Top | 48% | above £125,140 | same |

### Announced tax changes (legislated/announced, future-dated)

| Change | Effective | Source |
| --- | --- | --- |
| Dividend rates +2ppt: ordinary 10.75%, upper 35.75% (additional unchanged 39.35%) | **2026/27 (in force)** | [Budget 2025 OOTLAR](https://www.gov.uk/government/publications/budget-2025-overview-of-tax-legislation-and-rates-ootlar/budget-2025-overview-of-tax-legislation-and-rates-ootlar) |
| Savings income rates 22% / 42% / 47% | 6 April 2027 | same |
| Separate property income rates 22% / 42% / 47% | 6 April 2027 | same |
| Cash ISA limit £12,000 for under-65s (overall £20,000 unchanged) + anti-circumvention rules | 6 April 2027 | [ISA reform factsheet](https://www.gov.uk/government/publications/fiscal-events-2026-factsheets/isa-reform-2027-anti-circumvention-rules-factsheet) |
| NICs on salary-sacrificed pension contributions above £2,000/yr | April 2029 | [Employer Bulletin Dec 2025](https://www.gov.uk/government/publications/employer-bulletin-december-2025/december-2025-issue-of-the-employer-bulletin) |

### Pensions

| Figure | Value | Source |
| --- | --- | --- |
| Annual allowance | £60,000 (capped at 100% of earnings) | [pension schemes rates](https://www.gov.uk/government/publications/rates-and-allowances-pension-schemes/pension-schemes-rates); [annual allowance](https://www.gov.uk/tax-on-your-private-pension/annual-allowance) |
| AA taper | threshold income £200,000; adjusted income £260,000; −£1 per £2 over; floor £10,000 | same rates page; [tapered AA guidance](https://www.gov.uk/guidance/pension-schemes-work-out-your-tapered-annual-allowance) |
| MPAA | £10,000; triggered by first FAD income payment, first UFPLS, and related events (not by PCLS-only or standard lifetime annuity) | rates page; [PTM056520](https://www.gov.uk/hmrc-internal-manuals/pensions-tax-manual/ptm056520) |
| AA carry-forward | unused AA from previous 3 tax years usable (mechanics to re-verify at implementation) | [annual allowance](https://www.gov.uk/tax-on-your-private-pension/annual-allowance) |
| Relief at source | provider adds 20% basic-rate relief (25% top-up on net); higher/additional via assessment; Scottish variants apply | [pension tax relief](https://www.gov.uk/tax-on-your-private-pension/pension-tax-relief) |
| Net pay | contributions deducted pre-tax; full marginal relief automatic | same |
| Tax-free lump sum | up to 25%, capped by LSA £268,275 | [lump sum allowance](https://www.gov.uk/tax-on-your-private-pension/lump-sum-allowance); rates page |
| LSDBA | £1,073,100 | same |
| UFPLS | 25% of each payment tax-free, 75% taxed as income; triggers MPAA | [PTM063300](https://www.gov.uk/hmrc-internal-manuals/pensions-tax-manual/ptm063300) |
| Flexi-access drawdown | 25% PCLS at designation, income taxed at marginal rate (PAYE); MPAA on first income draw | [PTM062730](https://www.gov.uk/hmrc-internal-manuals/pensions-tax-manual/ptm062730) |
| NMPA | 55 now; **57 from 6 April 2028**; protected pension ages exist (out of scope v1) | [how you can take your pension](https://www.gov.uk/personal-pensions-your-rights/how-you-can-take-pension); [increasing NMPA policy paper](https://www.gov.uk/government/publications/increasing-normal-minimum-pension-age) |

### ISA / LISA

| Figure | Value | Source |
| --- | --- | --- |
| ISA annual allowance | £20,000 (page states 2026/27 explicitly) | [gov.uk/individual-savings-accounts](https://www.gov.uk/individual-savings-accounts) |
| LISA allowance | £4,000/yr, inside the £20,000 | [gov.uk/lifetime-isa](https://www.gov.uk/lifetime-isa) |
| LISA bonus | 25%, max £1,000/yr | same |
| LISA ages | open 18–39 (first payment before 40); contribute to 50; charge-free access at 60 (or first home ≤£450k, terminal illness, death) | same; [who can open](https://www.gov.uk/lifetime-isa/who-can-open-a-lifetime-isa); [withdrawing](https://www.gov.uk/lifetime-isa/withdrawing-money-from-your-lifetime-isa) |
| LISA withdrawal charge | 25% of amount withdrawn (net loss exceeds bonus) | [withdrawing](https://www.gov.uk/lifetime-isa/withdrawing-money-from-your-lifetime-isa) |

### State pension

| Figure | Value | Source |
| --- | --- | --- |
| Full new state pension | **£241.30/week** (£12,547.60/yr) for 2026/27 | [what you'll get](https://www.gov.uk/new-state-pension/what-youll-get); [DWP benefit & pension rates 2026–27](https://www.gov.uk/government/publications/benefit-and-pension-rates-2026-to-2027/proposed-benefit-and-pension-rates-2026-to-2027) |
| April 2026 uprating | 4.8%, earnings-driven (AWE 4.8% > CPI 3.8% > 2.5%) | [Government Actuary report on the 2026 up-rating order](https://www.gov.uk/government/publications/report-to-parliament-on-the-2026-re-rating-and-up-rating-orders/report-by-the-government-actuary-on-the-draft-social-security-benefits-up-rating-order-2026-and-the-draft-social-security-contributions-regulation) |
| Full basic (old) state pension | £184.90/week (context) | DWP rates page above |
| Qualifying years | 35 for full (transitional caveats for pre-2016 contracted-out records); 10 minimum | [what you'll get](https://www.gov.uk/new-state-pension/what-youll-get); [new state pension](https://www.gov.uk/new-state-pension) |
| Deferral | +1% per 9 weeks (~5.8%/yr); increments uprated by CPI | [deferring (post-2016)](https://www.gov.uk/deferring-state-pension/if-you-reach-state-pension-age-on-or-after-6-april-2016) |
| SPA now → 67 | 66 rising to 67: DOB 1960-04-06–1960-05-05 → 66y 1m, +1 month per DOB month to 1961-02-06–1961-03-05 → 66y 11m; DOB 1961-03-06–1977-04-05 → **67** (completes March 2028) | [SPA timetable](https://www.gov.uk/government/publications/state-pension-age-timetable/state-pension-age-timetable) |
| SPA 67 → 68 | legislated 2044–2046: DOB 1977-04-06–1978-04-05 phased; DOB ≥1978-04-06 → 68 | same |
| SPA review | Third review launched July 2025, ongoing (no change legislated as of 2026-08-01) | [third SPA review collection](https://www.gov.uk/government/collections/third-state-pension-age-review) |
| Triple lock policy | committed "for this parliament" (~2029); nothing legislated beyond | [Budget 2025 fact sheet](https://www.gov.uk/government/news/budget-2025-fact-sheet-cutting-the-cost-of-living) |

---

## Part 2 — Default assumptions (proposed; every one user-overridable)

Columns: assumption key ([domain-model.md §1](design/domain-model.md)) ·
default · basis. Recorded 2026-08-01.

### Economic

| Key | Default | Basis |
| --- | --- | --- |
| `inflation.cpi` | 2.0%/yr | OBR EFO March 2026: CPI at target from 2027 ([obr.uk EFO](https://obr.uk/efo/economic-and-fiscal-outlook-march-2026/)) |
| `earnings.growth.real` | 0.5%/yr | OBR EFO March 2026 medium-term real earnings growth |
| `returns.equity.real` | 4.0%/yr | Below long-run global equity history (~5% real); above the FCA intermediate rate (5% nominal − 2% CPI = 3% real) used as conservative cross-check ([COBS 13 Annex 2](https://www.handbook.fca.org.uk/handbook/COBS/13/Annex2.html): 2/5/8% nominal maxima, tax-advantaged) |
| `returns.bonds.real` | 0.5%/yr | Consistent with current gilt real yields ballpark and FCA lower rate; conservative |
| `returns.cash.real` | −0.5%/yr | Cash trails inflation after fees over long horizons |
| `volatility.equity` | 18%/yr | Long-run annual volatility of global equities (commonly cited 15–20%) |
| `volatility.bonds` | 7%/yr | Long-run annual volatility of investment-grade/gilt portfolios |
| `volatility.cash` | 1%/yr | Near-riskless nominal |
| `correlation.equity_bonds` | 0.2 | Long-run average; regime-dependent (label prominently) |
| `correlation.equity_cash` | 0.0 | — |
| `correlation.bonds_cash` | 0.2 | — |
| `fees.platform` | 0.25%/yr | Typical UK platform fee |
| `fees.fund` | 0.15%/yr | Typical index-tracker OCF |

### Longevity and horizon

| Key | Default | Basis |
| --- | --- | --- |
| `horizon.planning_age` | 95 | Covers roughly 1-in-4 longevity risk for a 65-year-old per ONS cohort life expectancy; exact values from ONS 2024-based cohort life tables ([ONS life expectancy calculator](https://www.ons.gov.uk/peoplepopulationandcommunity/healthandsocialcare/healthandlifeexpectancies/articles/lifeexpectancycalculator/2019-06-07)) |

### Policy futures (scenario-flippable)

| Key | Default | Basis |
| --- | --- | --- |
| `policy.state_pension.uprating` | `triple_lock` (modelled as CPI + 0.5%, the long-run earnings premium) | Alternative scenario value: `cpi`. Triple lock committed only to ~2029 (Budget 2025 fact sheet, Part 1) |
| `policy.tax.future_years` | Frozen to 2030/31 (legislated), then CPI-indexed | Freeze verified in Part 1; post-2031 indexation is a genuine assumption. Alternative: frozen indefinitely |

### Annuity rates (market snapshot — volatile; refresh before relying on)

| Key | Default | Basis |
| --- | --- | --- |
| `annuity.level.single.65` | 7.75%/yr per £ purchase | Which? market table, retrieved 2026-08-01, snapshot dated 2026-07-27 ([which.co.uk annuity rates](https://www.which.co.uk/money/pensions-and-retirement/accessing-your-pensions/annuities/annuity-rates-aQGfH6W5n2rm)); best rate 7.946% |
| `annuity.escalating3.single.65` | 5.47%/yr | same snapshot |
| `annuity.inflation_linked.single.65` | 5.5%/yr | Indicative only — secondary source ([IFA Magazine](https://ifamagazine.com/annuity-rates-hit-7-75-as-retirement-incomes-reach-18-year-high/)); weakest-sourced default in this register |
| `annuity.age_adjustment` | table TBD in Phase 5 | Per-age/type table to be built from a current market source when annuities are implemented; joint-life rates likewise |

---

## Open verification questions

Carried from the research pass (2026-08-01):

1. **FCA COBS intermediate inflation rate** — fetched mirror showed 2.00% in
   COBS 13 Annex 2 2.5R (long-standing value was 2.5%); canonical FCA page
   blocked extraction. Re-verify before citing COBS for inflation. (The
   2/5/8 and 1.5/4.5/7.5 return maxima were confirmed twice.)
2. **NMPA legislation vehicle** — the 2028 date and protections are
   confirmed on the gov.uk policy paper; the enacting statute (likely
   Finance Act 2022) was not confirmed on a fetched primary page.
3. **AA carry-forward mechanics** — 3-year headline verified; detailed
   ordering/membership rules to re-verify at implementation
   ([guidance](https://www.gov.uk/guidance/check-if-you-have-unused-annual-allowances-on-your-pension-savings)).
4. **Third SPA review deadline** (reported March 2029) — from secondary
   sources only; no legislated SPA change as of retrieval.
5. **ONS exact cohort values** — calculator is interactive; use the
   2024-based cohort life tables dataset for hard numbers when implementing
   the longevity default.
6. **OBR 50-year determinants** — medium-term EFO figures verified; the
   long-term determinants live in Fiscal risks & sustainability July 2026
   (PDF, unextracted) — worth mining for >30-year horizons.

## Change log

| Date | Change |
| --- | --- |
| 2026-08-01 | Initial register: 2026/27 figures verified; default assumptions proposed. |
