# HackSphere 3.0 GitHub OSINT

**Checked:** 26 October 2026 (IST)  
**Scope:** public GitHub repositories tied to HackSphere 3.0’s Smart Energy Microgrid & Demand Forecasting Engine, their code and visible commit history, and comparison with the two project snapshots supplied in this workspace.

## Executive read

I found **nine non-empty public repositories** that explicitly describe themselves as HackSphere 3.0 submissions or implement this exact challenge. This is a GitHub search count, not a confirmed event-wide total; private, unindexed, renamed, or not-yet-published repositories will be missed. I found no public ranking or judge result yet. The event listing gives a submission deadline of 28 October 2026.

The local `app/` snapshot is substantially more complete on paper than these three public codebases: it has a real inference-provider layer, persisted run history, a battery simulator and sizing lab, a polished dashboard, and a fallback path in `predict.py`. The workspace README reports published QuickML models and chronological RMSE values, but I have not independently verified the deployment, endpoint, or metrics as part of this OSINT pass.

The supplied context names `Avi36005/gridwise-hacksphere-3` as the source repository. GitHub’s public repository endpoint returned 404 during this check, so I could not inspect its remote history. There is no `.git` directory in this workspace. The only inspectable project history here is the contrast between `app 2/` and `app/`; those snapshots show the product’s evolution, but do not prove which person or AI tool authored it.

## Direct challenge matches

| Repository | Public evidence | What the code actually shows | Read-through |
| --- | --- | --- | --- |
| [Bucephala — Smart-Energy-Microgrid](https://github.com/krishnayyar999-sudo/Smart-Energy-Microgrid) | README names Team Bucephala and HackSphere 3.0, links a live Slate URL, and documents the required grid thresholds. | `predict.py` uses four fixed coefficients (intercept, temperature, humidity, solar) and does not call QuickML. It has no timestamp/hour feature. The repository includes the organizer `test_eval.py`. | Best visible deployment signal among the matches, but the checked-in predictor is a simple linear formula; the README’s QuickML architecture claim is not demonstrated by this inference entry point. The formula’s accuracy is not established by the repository files I inspected. |
| [Ritwika Saha — Hack-Sphere-3.0](https://github.com/Ritwika-Saha/Hack-Sphere-3.0) | README calls it a HackSphere 3.0 prototype and describes the specified API/dashboard flow. | The QuickML client explicitly raises `NotImplementedError`; the deployment guide calls the endpoint connection a placeholder. The controller applies the threshold/hour and overload rules. Tests and a FastAPI API scaffold are present. | Good structure and honest limitations, but it is a prototype rather than a working end-to-end forecast submission at the inspected revision. |
| [HackSphere-Energy-Forecasting](https://github.com/lasyasaimanasa/HackSphere-Energy-Forecasting) | Repository description is the exact challenge title. | Flask CSV dashboard, organizer contract test, rules controller, simple battery simulation, optional QuickML calls, and a local fallback. The fallback uses hour-of-day and temperature only; it ignores humidity and irradiance. Its code says fallback estimates are not QuickML predictions. | More functional than the scaffold, but QuickML failure raises an error rather than silently pretending the fallback came from the model. The fallback is a rough heuristic, not evidence of evaluated accuracy. |
| [Bharadwajcs-AI — HackSphere3.0-Microgrid](https://github.com/Bharadwajcs-AI/HackSphere3.0-Microgrid) | README explicitly names the exact challenge and describes a 24-hour model, dispatch controller, and battery dashboard. | README claims a gradient-boosting model and 10.03 kW CV RMSE; those claims were not independently validated in this pass. | Potentially a serious-looking submission based on its README, but claims need source/evaluation inspection before ranking it. |
| [Mohilc — HackSphere3.0hackathon](https://github.com/Mohilc/-HackSphere3.0hackathon) | README explicitly names the exact challenge. | React/Vite dashboard, Flask API, trained GradientBoostingRegressor and deployment guide are present in the repository search results. | A more complete full-stack competitor; verify actual model evaluation and live deployment before comparing it to Gridwise. |
| [Kahasini — hacksphere-energy-forecasting](https://github.com/kahasini2008-alt/hacksphere-energy-forecasting) | README uses the exact challenge title and lists the required thresholds. | README describes a local dashboard and QuickML integration as still being validated, with Catalyst deployment pending. | Relevant, but the checked-in README describes an unfinished submission. |
| [GridLens](https://github.com/neevmodh/GridLens) | README identifies HackSphere 3.0, provides a live Catalyst app, evidence documents, and says submission was confirmed. | Claims published QuickML XGB model, Data Store, 24-hour dispatch/battery plan, and successful 1,000-row evaluator. | One of the strongest publicly documented submissions. Its README is unusually explicit about validation evidence and limits; still, the reported live behavior is self-reported unless independently exercised. |
| [Voltide](https://github.com/vikky781/Voltide) | README identifies HackSphere 3.0 and Team Voidrun, links its Catalyst app and reports the official evaluator passed against a live QuickML endpoint. | Detailed model card claims chronological model comparisons, a locked test split, durable session history, and tests. | Also a strong apparent competitor. The repo makes better evidence claims than most, though those results remain repository-reported until independently reproduced. |
| [iblamesomeone — HackSphere-3.0](https://github.com/iblamesomeone/HackSphere-3.0) | Contains a project progress tracker for the exact HackSphere 3.0 microgrid task. | Tracker says local end-to-end app works, backend/UI and 13 tests are complete, while QuickML authentication/integration is still pending. | A legitimate project repo, but its own status record indicates the cloud model path was unfinished at the inspected revision. |

These direct challenge repos are recent. Some link live Catalyst apps; I did not exercise those sites. Public repo contents, commit dates, and self-reported test/model claims are not equivalent to an independent evaluation or the organizers’ final ranking.

## Nearby, but not direct challenge matches

- [Darshanpawar7/Bit_N_Build](https://github.com/Darshanpawar7/Bit_N_Build) is a different microgrid competition project. Its differentiators include a multi-agent community model, peer-to-peer energy transfers, household/EV flexibility, and an animated digital twin. It is a useful product/storytelling reference, but its problem statement and system scale differ from HackSphere’s fixed 24-row forecasting contract.
- [Zeba-Mushtaq/Microgrid-AI-Forecasting](https://github.com/Zeba-Mushtaq/Microgrid-AI-Forecasting) compares ANN, LSTM, random forest, and persistence models across load/solar/wind and reports a persistence baseline that beats its deep models for load. This is a reminder to benchmark simple baselines with chronological splits; it is not a HackSphere submission or a directly comparable dataset.

## What the visible repo history does and does not tell us

- The Lasyasaimanasa history has two ordinary feature commits: initial dashboard and then battery/dashboard improvements. The checked-in code supports a small Flask prototype, not claims about a Claude run.
- Ritwika’s two visible commits show an initial scaffold followed by a validation-order fix. Public commit metadata records the Git author, not whether Claude or another assistant contributed.
- The Bucephala repository history is small and mostly README changes in the latest visible entries. Its checked-in linear predictor is the stronger evidence for how it actually scores rows.
- No project-specific public Claude session, transcript, or verifiable Claude attribution was found. Commit message style, unsigned commits, and code patterns cannot establish AI authorship.

So, “how Claude did previous times” cannot be answered from public GitHub data. What can be compared locally is the earlier `app 2/` snapshot against the expanded `app/` snapshot. The latter adds a provider-aware `engine/inference.py`, feature generation for two model families, a more robust CLI/API fallback, Data Store support, more complete battery simulation/scenario sizing, and a much larger UI and test suite. `context.md` labels `app 2/` as an earlier milestone and `app/` as the active codebase. That is evidence of project evolution, not evidence about Claude’s contribution or quality across prior conversations.

## Competitive takeaways for Gridwise

1. **The hidden scoring interface still matters most.** The challenge weights prediction accuracy at 35%, deployment/functionality at 35%, switching logic at 20%, and UI at 10%. Make it easy for evaluators to run `predict.py` and confirm a finite value for every input row; keep the dashboard subordinate to that contract.
2. **Make QuickML use auditable.** In the inspected matches, one has a fixed formula despite describing QuickML, and another leaves QuickML as `NotImplementedError`. Gridwise’s strongest defensible distinction is an actually configured QuickML path plus a clearly disclosed local fallback. The current `predict.py` comments and docs should accurately identify which source produced each prediction.
3. **Show chronological validation, with a baseline.** Compare against a temperature/hour baseline and report the exact time splits, sample counts, and metric calculation. Avoid random splits on autocorrelated hourly data. This gives the claimed ensemble value evidence rather than relying on model names.
4. **Keep transition logic literal and inspectable.** Preserve `demand > 160 OR hour 18–22 inclusive` and `demand >= 200`, including equality cases. A separate battery simulator can be valuable, but label it simulated, and don’t let battery assumptions alter the required dispatch signal.
5. **Differentiate with useful operational depth.** The public challenge matches mostly show 24-hour charts and threshold badges. Gridwise’s scenario sizing, storage shortfall accounting, audit trail, and operator-oriented telemetry workflow are stronger differentiation if they remain easy to demo and clearly labeled as simulation.
6. **Don’t claim a contest win yet.** The public event listing says submissions remain open until 28 October 2026. I found no published results during this check.

## Sources checked

- [HackSphere 3.0 event listing](https://unstop.com/hackathons/hacksphere-30-birla-institute-of-technology-science-bits-pilani-hyderabad-campus-1771036)
- [Bucephala repository](https://github.com/krishnayyar999-sudo/Smart-Energy-Microgrid)
- [Ritwika Saha repository](https://github.com/Ritwika-Saha/Hack-Sphere-3.0)
- [Lasyasaimanasa repository](https://github.com/lasyasaimanasa/HackSphere-Energy-Forecasting)
- [Bit_N_Build related microgrid project](https://github.com/Darshanpawar7/Bit_N_Build)
- [Microgrid AI forecasting reference](https://github.com/Zeba-Mushtaq/Microgrid-AI-Forecasting)
- Local project materials: `context.md`, `app/README.md`, `app 2/README.md`, `app/predict.py`, and the corresponding engine modules.

## Limits

This review covers public repository metadata, selected source files, and visible commit history. It does not establish real deployed behavior, model accuracy, judging outcomes, or whether contributors used Claude. GitHub search is not exhaustive; the count of nine means nine non-empty public repositories I could verify as tied to this exact competition, not the event’s total submissions. A GitHub 404 can mean unavailable, renamed, deleted, or private; it does not identify which explanation applies.
