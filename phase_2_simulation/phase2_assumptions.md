# Assumptions

**Setup time**
* We will setup once workcell set (ie all of station 5) every time the lot size moves through those stations (cumulativly)
* It will lock the required number of operators to work on each station (ie if there is 5 stations - 5 operaotors must work on the reduction so if there is a setup time of 10 min per station (5 stations * 10 min / 5 operators = 10 min) - can change the number if differenet (more or less operators are working))
* This will happen for each product moving through the line - so each of the 3 products can be worked on simeltaneously with no setup times required until the batch/lot moves through the line

* (Updated) Setups are staggered: the trigger is still each new lot at a station, but each machine changes over on its own when it frees up, using one operator, and resumes immediately. The station no longer has to drain or wait for a machine in repair (`setup_staggered=True`; `False` restores the all-at-once group setup)

**Matching**
* Sub-assemblies are interchangeable within a model: in the mixed-model (cyclic/heijunka) policies, any keyboard, power strip and backlight of the same model can be matched (oldest first), regardless of lot
* In those policies keyboards and backlights are only built when their branch is behind the power-strip branch for that model (kit pull); otherwise the kit component is kept as a spare
* Lot-by-lot product runs still match within a lot (oldest first)

**Redutions**
* We will have a 75% reduction time on all workstations
* We implimented SPC on every workstation
