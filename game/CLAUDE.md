RUGBY LEAGUE MANAGER (RLM) GAME - STATE

CATEGORY	STATE	SUBSTATE	START_TRIGGER	END_TRIGGER	ATTACKER_BEHAVIOUR	DEFENDER_BEHAVIOUR	WORLD_UPDATE
EDGE_CASE	BUNKER	BUNKER_REVIEW_TRY	referee sends try decision upstairs	bunker decision	freeze	freeze	try awarded or denied
EDGE_CASE	BUNKER	BUNKER_REVIEW_NO_TRY	referee sends no‑try upstairs	bunker decision	freeze	freeze	try awarded or denied
EDGE_CASE	BUNKER	BUNKER_FOUL_PLAY	suspected foul play	bunker decision	freeze	freeze	penalty, sin bin, or send‑off
EDGE_CASE	BUNKER	BUNKER_IN_GOAL_DECISION	grounding unclear	bunker decision	freeze	freeze	dropout, 20m, or try
EDGE_CASE	SIMULTANEOUS_GROUNDING	ATTACKER_DEFENDER_SAME_TIME	attacker + defender ground ball simultaneously	referee ruling	freeze	freeze	held‑up or dropout depending on location
EDGE_CASE	SIMULTANEOUS_GROUNDING	TWO_ATTACKERS	two attackers ground ball	referee ruling	freeze	freeze	try awarded to attacking team
EDGE_CASE	SIMULTANEOUS_GROUNDING	TWO_DEFENDERS	two defenders ground ball	referee ruling	freeze	freeze	dropout or 20m depending on last touch
EDGE_CASE	BALL_HITS_POST	BALL_HITS_POST_IN_PLAY	ball hits goal post and rebounds	next event	play on	play on	ball live
EDGE_CASE	BALL_HITS_POST	BALL_HITS_POST_AND_GOES_DEAD	ball hits post then goes dead	whistle	freeze	freeze	20m optional restart
EDGE_CASE	BALL_HITS_POST	BALL_HITS_POST_AND_TRY	ball hits post and attacker grounds	whistle	celebrate	freeze	try awarded
EDGE_CASE	TOUCH_JUDGE	TOUCH_JUDGE_CONTACT	ball hits touch judge	whistle	freeze	freeze	scrum to team last in possession
EDGE_CASE	TOUCH_JUDGE	TOUCH_JUDGE_IN_PLAY	touch judge inadvertently obstructs	play continues	next event	play on	play on
EDGE_CASE	MULTI_BOUNCE_INGOAL	BALL_BOUNCES_THEN_TOUCH	ball bounces then goes into touch‑in‑goal	whistle	freeze	freeze	20m 7 tackle restart
EDGE_CASE	KICK_DEFLECTION	DEFLECTION_OFF_PLAYER	kick deflects off any player	next event	play on	play on	ball live
EDGE_CASE	KICK_DEFLECTION	DEFLECTION_OFF_OFFICIAL	kick deflects off referee	whistle	freeze	freeze	scrum to team last in possession
EDGE_CASE	FUMBLE_IN_GOAL	ATTACKER_FUMBLE_INGOAL	attacker fumbles in‑goal	grounding or dead	attacker attempts regather	defenders attempt grounding	try, dropout, or 20m
EDGE_CASE	FUMBLE_IN_GOAL	DEFENDER_FUMBLE_INGOAL	defender fumbles in‑goal	grounding or dead	attacker chases	defender attempts grounding	dropout or try
END_OF_PERIOD	FIRST_HALF_END	TIME_EXPIRED_40	clock hits 40:00	whistle	freeze	freeze	half ends, teams swap ends
END_OF_PERIOD	SECOND_HALF_END	TIME_EXPIRED_80	clock hits 80:00	whistle	freeze	freeze	match ends
END_OF_PERIOD	EXTRA_TIME_START	EXTRA_TIME_BEGIN	fulltime tied	kickoff taken	normal play	normal play	extra time begins
END_OF_PERIOD	EXTRA_TIME_GOLDEN_POINT	GOLDEN_POINT_BEGIN	extra time tied	kickoff taken	normal play	normal play	golden point begins
END_OF_PERIOD	MATCH_END_GOLDEN_POINT	GOLDEN_POINT_SCORE	field goal or try in GP	whistle	celebrate	freeze	match ends immediately
END_OF_PERIOD	MATCH_END_BLOWOUT	BLOWOUT_END	referee discretion	whistle	freeze	freeze	match ends
END_OF_PERIOD	INJURY_STOPPAGE	INJURY_STOP	serious injury	referee stops play	freeze	freeze	restart with PTB or scrum depending on cause
END_OF_PERIOD	BLOOD_BIN	BLOOD_RULE	blood rule triggered	player leaves field	team reshuffles	team reshuffles	substitution allowed
END_OF_PERIOD	SIN_BIN	SIN_BIN_START	foul play or repeated infringements	player leaves for 10 min	team attacks with extra man	team defends with 12	sin bin timer starts
END_OF_PERIOD	SEND_OFF	SEND_OFF_START	serious foul play	player dismissed	team attacks with extra man	team defends with 12 permanently	send‑off recorded
END_OF_PERIOD	CAPTAIN_CHALLENGE	CHALLENGE_DECISION	challenge initiated	decision made	freeze	freeze	decision overturned or upheld
END_OF_PERIOD	RESTART_AFTER_STOPPAGE	STOPPAGE_RESTART	stoppage resolved	restart taken	open play	open play	restart type depends on stoppage
GLOBAL	OFFSIDE	OFFSIDE_RUCK	defender inside 10m	whistle or 6‑again	attacker plays on	defender penalised	penalty or 6‑again
GLOBAL	OFFSIDE	OFFSIDE_KICK_CHASE	kicker’s teammates ahead of kicker	whistle	freeze	freeze	penalty to receiving team
GLOBAL	OFFSIDE	OFFSIDE_QUICK_TAP	defender not back 10m on quick tap	whistle	freeze	freeze	penalty advanced 10m
GLOBAL	INJURY	INJURY_MINOR	minor injury	play stops	freeze	freeze	PTB restart
GLOBAL	INJURY	INJURY_MAJOR	major injury	extended stoppage	freeze	freeze	restart determined by referee
GLOBAL	INJURY	HIA_ASSESSMENT	HIA triggered	player leaves field	team reshuffles	team reshuffles	substitution allowed
GLOBAL	BALL_STEAL	ONE_ON_ONE_STRIP	one‑on‑one strip attempt	ball stolen or retained	attacker resists	defender strips	turnover if successful
GLOBAL	BALL_STEAL	MULTI_DEFENDER_STRIP	illegal strip with multiple defenders	whistle	freeze	freeze	penalty to attackers
GLOBAL	MARKER	MARKER_SET	PTB begins	PTB ends	attacker plays ball	two defenders become markers	marker positions updated
GLOBAL	MARKER	MARKER_ILLEGAL	marker not square	whistle	freeze	freeze	penalty to attackers
GLOBAL	PLAY_THE_BALL	PTB_STANDARD	attacker plays ball	first receiver catches	attacker steps aside	defenders advance	tackle_count += 1
GLOBAL	PLAY_THE_BALL	PTB_INCORRECT	incorrect PTB	whistle	freeze	freeze	scrum to defenders
GLOBAL	PLAY_THE_BALL	PTB_QUICK	fast PTB	first receiver catches	attacker resets	defenders react late	ruck_speed = fast
GLOBAL	KICKING	KICK_IN_PLAY	attacker kicks	ball lands or is caught	chase	retreat or chase	ball trajectory
GLOBAL	KICKING	KICK_OUT_ON_FULL	kick goes out on full	whistle	freeze	freeze	Penalty to receiving team on halfway
GLOBAL	KICKING	KICK_OUT_ON_FULL	kick goes out on full	whistle	freeze	freeze	scrum or PTB depending on context
GLOBAL	KICKING	KICK_DEAD	kick goes dead	whistle	freeze	freeze	20m optional restart or dropout
GLOBAL	KICKING	CHARGE_DOWN	defender touches kick	ball regains ground	open play	chase	zero tackle if attackers regather
GLOBAL	GOAL_LINE	GOAL_LINE_DROP_OUT	defender forced in‑goal	dropout taken	attackers receive	defenders chase	restart from goal line
GLOBAL	GOAL_LINE	GOAL_LINE_HELD_UP	attacker held up	referee signal	attacker resets	defenders reset	PTB 10m out
GLOBAL	PENALTY	PENALTY_AWARDED	infringement	whistle	freeze	freeze	penalty to non‑offending team
GLOBAL	PENALTY	PENALTY_ADVANCED	dissent or repeated infringements	whistle	freeze	freeze	penalty advanced 10m
GLOBAL	PENALTY	PENALTY_KICK_TO_TOUCH	team elects kick to touch	ball finds touch	freeze	freeze	PTB at touch point
GLOBAL	SIN_BIN	SIN_BIN_START	player sent for 10 min	player leaves field	team attacks with extra man	team defends with 12	sin bin timer starts
GLOBAL	SIN_BIN	SIN_BIN_END	10 min expires	player returns	normal play	normal play	team restored to 13
GLOBAL	SEND_OFF	SEND_OFF_START	player dismissed	player leaves field	team attacks with extra man	team defends with 12 permanently	send‑off recorded
GLOBAL	KICKOFF_RESET	AFTER_SCORE	conversion result	kickoff taken	attackers prepare to receive	defenders prepare to kick	ball placed at halfway
GLOBAL	KICKOFF_RESET	TEAM_CHANGEOVER	team that conceded now kicks	kickoff taken	attackers prepare	defenders chase	possession flips
GLOBAL	KICKOFF_RESET	INCORRECT_KICKOFF	incorrect kickoff (offside, wrong spot)	whistle	freeze	freeze	kickoff retaken
GLOBAL	DROPOUT	GOAL_LINE_DROPOUT	defender forced in‑goal	dropout taken	attackers receive	defenders chase	restart from goal line
GLOBAL	DROPOUT	SHORT_DROPOUT	short dropout attempt	ball caught or contest ends	attackers contest	defenders contest	ball live, contest rules apply
GLOBAL	DROPOUT	INCORRECT_DROPOUT	incorrect dropout	whistle	freeze	freeze	dropout retaken
GLOBAL	SCRUM_RESET	EARLY_PUSH	early push detected	whistle	freeze	freeze	scrum reset
GLOBAL	SCRUM_RESET	FEED_NOT_STRAIGHT	feed not straight	whistle	freeze	freeze	scrum turnover
GLOBAL	SCRUM_RESET	PACK_COLLAPSE	scrum collapses	whistle	freeze	freeze	scrum reset
GLOBAL	RESTART_MISC	TAP_RESTART	penalty tap taken	first contact	attacker taps	defenders retreat	open play resumes
GLOBAL	RESTART_MISC	QUICK_TAP	quick tap allowed	tap taken	attacker taps immediately	defenders retreat	open play resumes
GLOBAL	RESTART_MISC	INCORRECT_TAP	incorrect tap	whistle	freeze	freeze	tap retaken
GLOBAL	BALL_RETURN	BALL_RETURN_INGOAL	defender catches ball in‑goal	defender runs out	attacker chases	defender runs	open play resumes
GLOBAL	BALL_RETURN	BALL_RETURN_TACKLED_INGOAL	defender tackled in‑goal	whistle	freeze	freeze	goal‑line dropout
GLOBAL	BALL_RETURN	BALL_RETURN_TOUCHDOWN	defender grounds ball in‑goal	whistle	freeze	freeze	20m optional restart
KICKOFF	KO	KO_NOT_10_UNTOUCHED	ball fails to travel 10m, untouched	whistle	freeze	freeze	PTB to receiving team 10m from halfway, 10m in from touch
KICKOFF	KO	KO_NOT_10_TOUCHED_RCVR	ball <10m, receiving team touches ball	first tackle contact	open play begins	chase begins	pipeline_state = NONE
KICKOFF	KO	KO_NOT_10_TOUCHED_KICKER	ball <10m, kicking team touches first	whistle	freeze	freeze	PTB to receiving team at touch point or 10m mark
KICKOFF	KO	KO_OUT_ON_FULL_TOUCH	ball goes out on full over touch line	whistle	freeze	freeze	PTB to receiving team 10m from halfway, 10m in from touch
KICKOFF	KO	KO_OUT_ON_FULL_INGOAL	ball goes out on full over touch‑in‑goal	whistle	freeze	freeze	20m optional restart to receiving team
KICKOFF	KO	KO_KICK_DEAD_ON_FULL	ball goes dead in‑goal on full	whistle	freeze	freeze	20m optional restart
KICKOFF	KO	KO_KICK_DEAD_BOUNCE	ball bounces then goes dead in‑goal	whistle	freeze	freeze	20m optional restart
KICKOFF	KO	KO_LIVE	ball caught clean past 10m	first tackle contact	open play begins	chase begins	pipeline_state = NONE
KICKOFF	KO	KO_LIVE_INGOAL_RETURN	ball caught in‑goal, attacker runs out	first tackle contact	open play	chase	pipeline_state = NONE
KICKOFF	KO	KO_LIVE_INGOAL_TACKLED	ball caught in‑goal, tackled in‑goal	whistle	freeze	freeze	goal‑line dropout
KICKOFF	KO	KO_OVER_SIDE_LINE_BOUNCE	ball bounces then goes into touch	whistle	freeze	freeze	scrum to receiving team at Y‑axis of exit
KICKOFF	KO	KO_SECOND_HALF	half‑time ends	kickoff taken	same as KO	same as KO	swap ends before kickoff
OPEN_PLAY	EVENT	TACKLE_COMPLETE	ball carrier grounded + held	referee calls held	play‑the‑ball setup	retreat 10m	ruck_x updated
OPEN_PLAY	EVENT	HELD_UP	attacker held up in‑goal	referee calls held‑up	attacker resets to 10m	defenders reset	goal‑line dropout OR 10m PTB (NRL rule)
OPEN_PLAY	EVENT	CHARGE_DOWN	defender touches kick without control	ball regains ground	play continues	chase	zero tackle reset if regathered by attackers
OPEN_PLAY	EVENT	ZERO_TACKLE	kick regathered by receiving team	first tackle contact	open play	chase	tackle_count = 0
OPEN_PLAY	EVENT	SEVEN_TACKLE_SET	ball kicked dead by attackers	restart taken	open play	chase	tackle_count = 0, restart at 20m
OPEN_PLAY	EVENT	QUICK_20M_RESTART	ball dead in‑goal by attackers	attacker taps ball	open play	defenders retreat	restart from 20m, defenders must be onside
OPEN_PLAY	EVENT	KNOCK_ON	ball lost forward	scrum formed	freeze	freeze	SCRUM state
OPEN_PLAY	EVENT	FORWARD_PASS	illegal forward pass	scrum formed	freeze	freeze	SCRUM state
OPEN_PLAY	EVENT	ACCIDENTAL_OFFSIDE	teammate accidentally offside	whistle	freeze	freeze	scrum to non‑offending team
OPEN_PLAY	EVENT	OUT_OF_PLAY_TOUCH	ball or carrier into touch	restart	freeze	freeze	RESTART_TOUCH
OPEN_PLAY	EVENT	OUT_OF_PLAY_INGOAL	attacker into in‑goal then out	whistle	freeze	freeze	20m optional restart
OPEN_PLAY	EVENT	CONTACT	defender enters tackle radius	next frame	contact animation	tackler locks, others chase	pipeline_state = DRAG
OPEN_PLAY	EVENT	PENALTY_HIGH	high tackle	whistle	freeze	freeze	penalty to attackers
OPEN_PLAY	EVENT	PENALTY_RUCK	ruck infringement	whistle	freeze	freeze	penalty to attackers
OPEN_PLAY	EVENT	PENALTY_OFFSIDE	offside inside 10m	whistle	freeze	freeze	penalty to attackers
OPEN_PLAY	EVENT	SIX_AGAIN_RUCK	ruck infringement (restart)	referee signal	open play	open play	tackle_count reset to 0
OPEN_PLAY	EVENT	SIX_AGAIN_OFFSIDE	offside restart	referee signal	open play	open play	tackle_count reset to 0
OPEN_PLAY	EVENT	FIELD_GOAL	attacker kicks field goal	whistle	celebrate/freeze	freeze/reset	score update + KO
OPEN_PLAY	EVENT	TRY	attacker grounds ball legally	whistle	celebrate	freeze	score update + conversion attempt
OPEN_PLAY	EVENT	NO_TRY_HELD_UP	attacker held up	referee signal	reset	reset	PTB 10m out
OPEN_PLAY	EVENT	NO_TRY_KNOCK_ON	knock‑on in‑goal	whistle	freeze	freeze	20m optional restart
OPEN_PLAY	EVENT	END_OF_FIRST_HALF	clock hits 40:00	whistle	freeze	freeze	half ends
OPEN_PLAY	EVENT	END_OF_SECOND_HALF	clock hits 80:00	whistle	freeze	freeze	match ends
OPEN_PLAY	EVENT	PASS	attacker passes ball to teammate	ball caught cleanly	new ball carrier runs or sets	defenders adjust and chase	play on
OPEN_PLAY	EVENT	INTERCEPTION_CLEAN	defender catches opposition pass cleanly	first contact	attacker becomes defender	defender becomes attacker	possession switches
OPEN_PLAY	EVENT	INTERCEPTION_KNOCK_ON	defender attempts interception but knocks on	whistle	freeze	freeze	SCRUM state
OPEN_PLAY	EVENT	INTERCEPTION_KNOCK_BACK	defender knocks ball backwards attempting interception	play on	attacker regathers or defender regathers	chase	play on
OPEN_PLAY	EVENT	INTERCEPTION_INGOAL	defender intercepts inside in‑goal	first contact	attacker becomes defender	defender becomes attacker	20m restart or play on depending on momentum
OPEN_PLAY	EVENT	LOOSE_BALL_RECOVERED_ATTACK	ball loose on ground	ball secured	attacker regathers	defenders chase	play on
OPEN_PLAY	EVENT	LOOSE_BALL_RECOVERED_DEFENCE	ball loose on ground	ball secured	attacker becomes defender	defender becomes attacker	possession switches
OPEN_PLAY	EVENT	FUMBLE_BACKWARDS	ball dropped backwards	ball regathered	attacker regathers	defenders chase	play on
OPEN_PLAY	EVENT	BALL_STRIPPED_LOOSE	strip attempt knocks ball loose	ball hits ground	attacker or defender contest	chase	ball_state = LOOSE
OPEN_PLAY	EVENT	BALL_ON_GROUND_CONTEST	ball loose and contested	ball secured	attacker or defender regathers	chase	possession determined by regather
OPEN_PLAY	EVENT	PASS_BEHIND	receiver over‑runs pass	ball hits ground	attacker regathers	defenders chase	play on
OPEN_PLAY	EVENT	PASS_MISCOMMUNICATION	no receiver present	ball hits ground	attacker regathers	defenders chase	play on
OPEN_PLAY	EVENT	PASS_INTERCEPT_CONTEST	both players contest pass	ball secured	attacker or defender regathers	chase	possession determined by regather
OPEN_PLAY	EVENT	KICK_OUT_ON_FULL	kick goes out on full	whistle	freeze	freeze	penalty or scrum depending on location
OPEN_PLAY	EVENT	KICK_40_20	kick bounces in field then into touch	whistle	celebrate	defenders reset	attacking scrum
OPEN_PLAY	EVENT	KICK_20_40	kick from defensive 20m bounces then into touch	whistle	celebrate	defenders reset	attacking scrum
OPEN_PLAY	EVENT	KICK_IN_GOAL_TRAPPED	defender trapped in‑goal with ball	whistle	freeze	freeze	goal‑line dropout
OPEN_PLAY	EVENT	KICK_IN_GOAL_DEAD	attacker kicks ball dead	restart	open play	defenders retreat	7 tackle set
OPEN_PLAY	EVENT	KICK_CONTEST_WON_ATTACK	bomb contested, attacker wins	first contact	attacker continues run	defenders chase	play on
OPEN_PLAY	EVENT	KICK_CONTEST_WON_DEFENCE	bomb contested, defender wins	first contact	attacker becomes defender	defender becomes attacker	possession switches
OPEN_PLAY	EVENT	KICK_FUMBLE	defender drops kick	whistle	freeze	freeze	SCRUM state
OPEN_PLAY	EVENT	TACKLE_BREAK	attacker breaks tackle	play on	attacker continues run	defenders chase	play on
OPEN_PLAY	EVENT	TACKLE_MISSED	defender misses tackle	play on	attacker continues run	defenders chase	play on
OPEN_PLAY	EVENT	SECOND_EFFORT_PENALTY	defender holds too long after held call	whistle	freeze	freeze	penalty to attackers
OPEN_PLAY	EVENT	DANGEROUS_THROW	dangerous lifting tackle	whistle	freeze	freeze	penalty + possible sin bin
OPEN_PLAY	EVENT	SHOULDER_CHARGE	illegal shoulder charge	whistle	freeze	freeze	penalty to attackers
OPEN_PLAY	EVENT	LATE_TACKLE	tackle after kick or pass	whistle	freeze	freeze	penalty to attackers
OPEN_PLAY	EVENT	BALL_HELD_UP_FIELD	attacker held up in field of play	referee calls held	attacker resets	defenders reset	PTB at point of held
OPEN_PLAY	EVENT	BALL_FORCED_INGOAL_DEFENCE	defender grounds ball in‑goal	whistle	freeze	freeze	goal‑line dropout
OPEN_PLAY	EVENT	BALL_FORCED_INGOAL_ATTACK	attacker grounds ball in‑goal	whistle	celebrate	freeze	TRY awarded
OPEN_PLAY	EVENT	BALL_KNOCKED_ON_INGOAL	knock‑on in‑goal	whistle	freeze	freeze	20m optional restart
OPEN_PLAY	EVENT	ADVANTAGE_PLAY_ON	referee signals advantage	play on	attacker continues	defenders chase	advantage active
OPEN_PLAY	EVENT	ADVANTAGE_OVER	advantage ends	whistle	freeze	freeze	return to original infringement
OPEN_PLAY	EVENT	SIX_AGAIN_DOWNTOWN	offside chasers inside 10m on kick	referee signal	open play	open play	tackle_count reset to 0
OPEN_PLAY	EVENT	SIX_AGAIN_HAND_IN_RUCK	defender hand on ball	referee signal	open play	open play	tackle_count reset to 0
OPEN_PLAY	EVENT	TIME_OFF	referee stops clock	freeze	freeze	freeze	clock stopped
OPEN_PLAY	EVENT	TIME_ON	referee restarts clock	play on	play on	play on	clock running
OPEN_PLAY	EVENT	INJURY_STOPPAGE	player down injured	whistle	freeze	freeze	medical stoppage
OPEN_PLAY	EVENT	INTERCHANGE	team makes substitution	restart	reset	reset	player swapped
OPEN_PLAY	EVENT	SIN_BIN	player sent for 10 minutes	whistle	freeze	freeze	team reduced by 1
OPEN_PLAY	EVENT	SEND_OFF	player dismissed	whistle	freeze	freeze	team reduced permanently
OPEN_PLAY	EVENT	LONG_KICK	attacker kicks long for territory	ball travels deep	attacker chase	defenders retreat and sweep	ball_state = KICKED
OPEN_PLAY	EVENT	BOMB_KICK	attacker kicks high bomb	ball reaches high apex	attacker chase and contest	defenders prepare catch	ball_state = KICKED_HIGH
OPEN_PLAY	EVENT	GRUBBER_KICK	attacker grubbers along ground	ball skids and bounces low	attacker chase	defenders turn and scramble	ball_state = KICKED_GROUND
OPEN_PLAY	EVENT	CHIP_KICK	attacker chip kicks over line	ball pops over defenders	attacker chase	defenders turn and contest	ball_state = KICKED_SHORT
OPEN_PLAY	EVENT	CROSSFIELD_KICK	attacker kicks wide crossfield bomb	ball travels laterally	winger chases	defenders shift and contest	ball_state = KICKED_CROSS
OPEN_PLAY	EVENT	TORPEDO_KICK	attacker kicks spiral bomb	ball wobbles unpredictably	attacker chase	defenders prepare difficult catch	ball_state = KICKED_SPIRAL
OPEN_PLAY	EVENT	DRIBBLE_KICK	attacker dribbles short rolling kick	ball rolls slowly	attacker chase	defenders rush to secure	ball_state = KICKED_DRIBBLE
OPEN_PLAY	EVENT	FLOATER_KICK	attacker kicks floating wobble bomb	ball floats unpredictably	attacker chase	defenders adjust to wobble	ball_state = KICKED_FLOAT
OPEN_PLAY	EVENT	OFFLOAD_SUCCESS	attacker offloads ball cleanly	ball caught	new ball carrier runs	defenders chase	play on
OPEN_PLAY	EVENT	OFFLOAD_KNOCK_ON	attacker offloads but loses ball forward	whistle	freeze	freeze	SCRUM state
OPEN_PLAY	EVENT	OFFLOAD_KNOCK_BACK	offload hits hand and goes backwards	ball regathered	attacker regathers or teammate regathers	defenders chase	play on
OPEN_PLAY	EVENT	OFFLOAD_HITS_GROUND	offload hits ground but not forward	ball loose	attacker or defender contest	chase	ball_state = LOOSE
OPEN_PLAY	EVENT	OFFLOAD_INTERCEPTED	defender intercepts offload	first contact	attacker becomes defender	defender becomes attacker	possession switches
PENALTY_CONVERSION	LINING_UP	STANDARD	penalty awarded	kicker begins run	kicker prepares	defenders hold line	none
PENALTY_CONVERSION	KICKING	STRIKE	kicker runs	ball kicked	kick animation	defenders hold line	ball trajectory
PENALTY_CONVERSION	SUCCESS	BETWEEN_POSTS	ball goes between posts	whistle	celebrate	freeze/reset	score update + KO
PENALTY_CONVERSION	FAIL	MISSED_BETWEEN_POSTS	ball misses	whistle	freeze	freeze	20m dropout
PENALTY_CONVERSION	FAIL_IN_PLAY	MISSED_LANDS_IN_FIELD	ball misses + lands in field	ball caught/grounded	open play	open play	open play continues
PENALTY_CONVERSION	FAIL_DEAD	MISSED_GOES_DEAD	ball misses + goes dead on full	whistle	freeze	freeze	20m dropout
RESTART_20M	RESTART_20M	OPTIONAL_TAP	ball dead in‑goal by attackers	tap taken	attacker taps	defenders retreat	restart from 20m, tackle_count = 0
RESTART_20M	RESTART_20M	QUICK_OPTIONAL_TAP	ball dead in‑goal by attackers	quick tap taken	attacker taps immediately	defenders retreat	restart from 20m, tackle_count = 0
RESTART_20M	RESTART_20M	SEVEN_TACKLE_SET	ball kicked dead by attackers	restart taken	attacker taps	defenders retreat	tackle_count = 0, restart from 20m
RESTART_TOUCH	RESTART_TOUCH	KICK_TO_TOUCH_PENALTY	penalty kick finds touch	restart taken	freeze	freeze	PTB at point ball crossed touch
RESTART_TOUCH	RESTART_TOUCH	KICK_TO_TOUCH_GENERAL_PLAY	kick in general play finds touch	restart taken	freeze	freeze	scrum at point ball crossed touch
RESTART_TOUCH	RESTART_TOUCH	QUICK_RESTART_TOUCH	ball into touch, quick restart allowed	tap taken	attackers quick tap	defenders retreat	restart at touch point
RESTART_TOUCH	RESTART_TOUCH	BALL_KNOCKED_INTO_TOUCH	knock‑on into touch	restart taken	freeze	freeze	scrum to non‑offending team
SCRUM	SCRUM_FORM	KNOCK_ON	knock‑on called	ball fed	pack + feed	pack	ball to halfback, tackle_count = 0
SCRUM	SCRUM_FORM	FORWARD_PASS	forward pass called	ball fed	pack + feed	pack	ball to halfback, tackle_count = 0
SCRUM	SCRUM_FORM	ACCIDENTAL_OFFSIDE	accidental offside	ball fed	pack + feed	pack	ball to halfback, tackle_count = 0
SCRUM	SCRUM_FORM	TOUCH_OPTION_SCRUM	team elects scrum after ball into touch	ball fed	pack + feed	pack	ball to halfback, tackle_count = 0
SCRUM	SCRUM_EXIT	SCRUM_WON	ball out of scrum	first contact	open play	chase	pipeline_state = NONE
SCRUM	SCRUM_EXIT	SCRUM_LOST	ball stolen or lost	first contact	open play	chase	turnover registered
TACKLE PIPELINE	TACKLE	STANDARD_DRAG	tackle initiated + ball carrier moving	drag_end	attacker dragged backwards/sideways	tacklers drag, others chase	ruck_x updated continuously
TACKLE PIPELINE	TACKLE	HELD_UP_DRAG	tackle initiated in‑goal + attacker not grounded	referee calls held‑up	attacker held upright	defenders hold upright	PTB 10m out
TACKLE PIPELINE	TACKLE	MOMENTUM_STOPPED	attacker momentum ceases	referee calls held	attacker braces	tacklers lock	ruck_x fixed
TACKLE PIPELINE	TACKLE	STRIP_ATTEMPT	defender attempts one‑on‑one strip	ball lost or retained	attacker resists	defender strips or fails	knock‑on or play‑on depending on outcome
TACKLE PIPELINE	RETREAT	RETREAT_10M	tackle complete	retreat_end	attacker stands	all defenders retreat 10m	offside line updated
TACKLE PIPELINE	RETREAT	RETREAT_GOALLINE	tackle complete inside 10m	retreat_end	attacker stands	defenders retreat to goal line	offside line = tryline
TACKLE PIPELINE	RETREAT	RETREAT_PENALTY_ADV	penalty advantage signalled	advantage ends	attacker stands	defenders retreat 10m	advantage flag cleared
TACKLE PIPELINE	SETTLE	SETTLE_LINE	retreat_end	settle_end	attacker resets	defenders form line	defensive_line_x updated
TACKLE PIPELINE	SETTLE	SETTLE_GOALLINE	retreat_end inside 10m	settle_end	attacker resets	defenders form goal‑line defence	defensive_line_x = tryline
TACKLE PIPELINE	HOLD	HOLD_STANDARD	settle_end	hold_end	attacker waits for PTB	defenders hold line	none
TACKLE PIPELINE	HOLD	HOLD_MARKER_SET	marker assigned	hold_end	attacker waits	two defenders become markers	marker positions updated
TACKLE PIPELINE	GETUP	GETUP_STANDARD	hold_end	getup_end	attacker stands	defenders hold line	none
TACKLE PIPELINE	GETUP	GETUP_SLOW_RUCK	slow ruck detected	getup_end	attacker stands slower	defenders hold line	ruck_speed = slow
TACKLE PIPELINE	GETUP	GETUP_FAST_RUCK	fast ruck detected	getup_end	attacker stands faster	defenders hold line	ruck_speed = fast
TACKLE PIPELINE	EXIT	CHASE_ALLOWED	getup_end	next tackle	open play resumes	defenders chase	pipeline_state = NONE
TACKLE PIPELINE	EXIT	PTB_COMPLETE	attacker plays the ball	first receiver catches	open play resumes	defenders advance	tackle_count += 1
TACKLE PIPELINE	EXIT	PTB_ERROR	incorrect PTB	whistle	freeze	freeze	scrum to defenders
TRY_CONVERSION	LINING_UP	STANDARD	try awarded	kicker begins run	kicker prepares	defenders freeze	none
TRY_CONVERSION	KICKING	STRIKE	kicker runs	ball kicked	kick animation	defenders freeze	ball trajectory
TRY_CONVERSION	SUCCESS	BETWEEN_POSTS	ball passes between posts	whistle	celebrate	freeze/reset	score update + KO
TRY_CONVERSION	FAIL	MISSED_BETWEEN_POSTS	ball misses	whistle	freeze	freeze	20m optional restart


ATTACK PIPELINE						
STATE	START	END	MIN	MAX	WHAT HAPPENS	WORLD UPDATE
BC_TACKLE	tackle_time	tackle_end	0.5	3.5	BC hit, wrapped, wrestle, driven, grounded OR standing (TES dominance decides)	update last_ruck_x (moving tackle)
HELD_RELEASE	tackle_end	release_called	0.3	0.8	Ref calls Held → Release. BC prepares to get up (or PTB if standing). TES checks slow peel etc.	freeze ruck_x, record held_start_time, allow marker_pre_positioning, no retreat allowed
BC_GETUP	release_called	getup_end	1	3	BC regains feet and resets stance. Skipped if standing_tackle = True. Timing modified by: quick_ptb_rating, dominance_factor, drive_back_distance	update bc_feet_position, update ruck_orientation
BC_PTB	getup_end	ball_played	0.5	1	BC plays the ball. Dominance affects speed. Standing tackle → faster PTB. TES checks PTB infringements	update ball_played_time, set ball_out = False, update ptb_location
BC_EXIT	ball_played	next tackle	0	∞	BC pipeline ends, open play resumes	set ball_out = True, update attacking_line_anchor
                        
                        
DEFENCE PIPELINE						
STATE	START	END	MIN	MAX	WHAT HAPPENS	WORLD UPDATE
DEF_TACKLE	tackle_time	tackle_end	0.5	3.5	Tacklers complete tackle; both teams in open play. TES handles dominance, joiners, drive‑back	update last_ruck_x (same as BC)
HELD_RELEASE	tackle_end	release_called	0.5	1	Tacklers release BC and peel away. No retreat allowed. TES checks slow peel, flop, crusher, strip-after-held	freeze ruck_x, record held_start_time, allow marker_pre_positioning, no retreat_vectors
DEF_RETREAT	release_called	retreat_end	0	∞	All defenders retreat to 10m. Retreat speed modified by dominance. Drive‑back increases distance	update offside_line = ruck_x + 10m, update retreat_vectors, update marker_paths
DEF_SETTLE	retreat_end	settle_end	0.2	no max	Defensive line straightens, spacing enforced. Dominance affects settle speed	update defensive_line_anchor, enforce spacing grid
DEF_HOLD	settle_end	hold_end	0.3	no max	Line frozen, markers locked. Wait for ball_out	lock marker_positions, freeze defensive_line_x
DEF_OFFSIDE_CHECK	ball_played	ball_out	—	—	Any defender inside 10m = offside. Advantage rule applies	evaluate offside_flags, apply penalty triggers
DEF_EXIT	ball_out	next tackle	0	∞	Line can advance; open play resumes	unlock defensive_line, allow forward movement


🏉 NRL RUCK ENGINE — CHEAT SHEET (PRINTABLE EDITION)
(Colour‑coded with emojis, no code blocks, no diagrams — just clean, structured info.)
⭐ COLOUR LEGEND
    • 🟦 TES‑controlled state
    • 🟨 Referee / infringement logic
    • 🟩 Speeds up (quick PTB, passive dominance, standing tackle)
    • 🟥 Slows down (dominant tackle, drive‑back)
    • 🟪 World update
⭐ TES — TACKLE ENGAGEMENT SYSTEM (TOP‑LEVEL FLOW)
1. Engagement Phase 🟦 BC_TACKLE / DEF_TACKLE
    • Joiners
    • Dominance
    • Momentum
    • Drive distance
    • Standing vs grounded 🟪 update last_ruck_x
Interrupts during engagement:
    • 🟦 Break tackle → 🟩 BREAK_TACKLE (play on)
    • 🟨 Penalty (high, flop, crusher, late) → 🟥 PENALTY
    • 🟦 Strip attempt
        ○ success → 🟩 STRIP_SUCCESS
        ○ fail → 🟥 FUMBLE → knock_on or knock_back
    • 🟦 Offload attempt
        ○ success → 🟩 OFFLOAD_SUCCESS
        ○ fail → 🟥 FUMBLE → knock_on or knock_back
2. Tackle Completion 🟦 TES checks:
    • BC grounded
    • dominance threshold
    • drive stopped → 🟦 HELD_CALLED 🟪 freeze ruck_x, record held_start_time
3. HELD → RELEASE Micro‑Phase (0.3–0.8s)
    • Tacklers must release
    • BC cannot get up
    • No retreat allowed
    • Marker pre‑positioning allowed 🟨 TES checks: slow peel, flop, crusher, strip‑after‑held
Release outcomes:
    • 🟨 Release penalty → 🟥 PENALTY
    • 🟦 Timeout → 🟦 RELEASE_CALLED
    • 🟦 All tacklers released → 🟦 RELEASE_CALLED
4. TES Complete
    • BC → GETUP
    • DEF → RETREAT 🟪 world transitions to ruck state
⭐ ATTACK PIPELINE (BC PIPELINE)
BC_TACKLE 0.5–3.5s
    • Wrestle, drive, grounded or standing 🟥 Dominance slows 🟥 Drive‑back slows 🟩 Passive tackle speeds 🟪 update last_ruck_x
HELD_RELEASE 0.3–0.8s
    • BC prepares to get up or PTB 🟨 slow peel / flop / crusher checks 🟪 freeze ruck_x, no retreat
BC_GETUP 1.0–3.0s 🟩 quick_ptb_rating speeds 🟥 dominance slows 🟥 drive‑back slows 🟩 standing_tackle skips entirely 🟪 update bc_feet_position, ruck_orientation
BC_PTB 0.5–1.0s 🟩 passive tackle → faster 🟥 dominant tackle → slower 🟩 standing tackle → faster 🟨 PTB infringement checks 🟪 update ball_played_time, ptb_location
BC_EXIT
    • Open play resumes 🟪 ball_out = True, update attacking_line_anchor
⭐ DEFENCE PIPELINE
DEF_TACKLE 0.5–3.5s 🟥 dominance increases control 🟩 passive tackle shortens 🟪 update last_ruck_x
HELD_RELEASE 0.5–1.0s 🟨 slow peel / flop / crusher checks 🟪 freeze ruck_x, no retreat_vectors
DEF_RETREAT 0–∞ 🟩 passive tackle → faster retreat 🟥 dominant tackle → slower retreat 🟥 drive‑back increases retreat distance 🟪 update offside_line, retreat_vectors, marker_paths
DEF_SETTLE 0.2–∞ 🟩 passive tackle → faster settle 🟥 dominant tackle → slower settle 🟪 update defensive_line_anchor, spacing grid
DEF_HOLD 0.3–∞
    • Line frozen 🟪 lock marker_positions, freeze defensive_line_x
DEF_OFFSIDE_CHECK
    • ball_played → ball_out 🟨 advantage applies 🟪 evaluate offside_flags, apply penalties
DEF_EXIT
    • Open play resumes 🟪 unlock defensive_line
⭐ TIMING MODIFIERS (GLOBAL)
Dominance Factor 🟩 Passive tackle → faster GETUP/PTB 🟥 Dominant tackle → slower GETUP/PTB
Quick PTB Rating 🟩 Hookers/fast players → faster GETUP 🟥 Props → slower GETUP
Standing Tackle 🟩 Skip GETUP 🟩 Faster PTB 🟪 ruck_orientation stable
Drive‑Back Distance 🟥 Slower GETUP 🟥 Longer retreat distance 🟪 offside_line shifts
⭐ BALL OUTCOMES
FUMBLE
    • classify by ball direction 🟥 knock_on → turnover 🟩 knock_back → play on
STRIP
    • success → turnover
    • fail → fumble
OFFLOAD
    • success → new BC
    • fail → fumble
⭐ REFEREE LOGIC (TES)
🟨 High tackle 🟨 Flop 🟨 Crusher 🟨 Strip‑after‑held 🟨 Late contact 🟨 Early PTB 🟨 Early retreat 🟨 PTB off the mark 🟨 Double movement
⭐ ADVANTAGE RULE (EVENT BUS)
If TES fires:
    • knock_on
    • penalty
Event Bus decides: 🟩 advantage_active → delay whistle 🟥 no advantage → immediate whistle
⭐ WORLD UPDATE SUMMARY
🟪 last_ruck_x 🟪 ruck_orientation 🟪 offside_line 🟪 retreat_vectors 🟪 marker_paths 🟪 defensive_line_anchor 🟪 spacing grid 🟪 ball_out 🟪 ptb_location
