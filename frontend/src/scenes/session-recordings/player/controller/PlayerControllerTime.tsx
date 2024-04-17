import { LemonButton, Tooltip } from '@posthog/lemon-ui'
import clsx from 'clsx'
import { useActions, useValues } from 'kea'
import { dayjs } from 'lib/dayjs'
import { useKeyHeld } from 'lib/hooks/useKeyHeld'
import { IconSkipBackward } from 'lib/lemon-ui/icons'
import { capitalizeFirstLetter, colonDelimitedDuration } from 'lib/utils'
import { ONE_FRAME_MS, sessionRecordingPlayerLogic } from 'scenes/session-recordings/player/sessionRecordingPlayerLogic'

import { playerSettingsLogic, TimestampFormat } from '../playerSettingsLogic'
import { seekbarLogic } from './seekbarLogic'

export function Timestamp(): JSX.Element {
    const { logicProps, currentPlayerTime, currentTimestamp, sessionPlayerData } =
        useValues(sessionRecordingPlayerLogic)
    const { isScrubbing, scrubbingTime } = useValues(seekbarLogic(logicProps))
    const { timestampFormat } = useValues(playerSettingsLogic)
    const { setTimestampFormat } = useActions(playerSettingsLogic)

    const startTimeSeconds = ((isScrubbing ? scrubbingTime : currentPlayerTime) ?? 0) / 1000
    const endTimeSeconds = Math.floor(sessionPlayerData.durationMs / 1000)

    const fixedUnits = endTimeSeconds > 3600 ? 3 : 2

    return (
        <LemonButton
            data-attr="recording-timestamp"
            onClick={() =>
                setTimestampFormat(timestampFormat === 'relative' ? TimestampFormat.Absolute : TimestampFormat.Relative)
            }
            active
        >
            {timestampFormat === TimestampFormat.Relative ? (
                <>
                    {colonDelimitedDuration(startTimeSeconds, fixedUnits)} /{' '}
                    {colonDelimitedDuration(endTimeSeconds, fixedUnits)}
                </>
            ) : (
                <>
                    {currentTimestamp
                        ? dayjs(currentTimestamp).tz('UTC').format('DD/MM/YYYY, HH:mm:ss')
                        : '--/--/----, 00:00:00'}{' '}
                    UTC
                </>
            )}
        </LemonButton>
    )
}

export function SeekSkip({ direction }: { direction: 'forward' | 'backward' }): JSX.Element {
    const { seekForward, seekBackward } = useActions(sessionRecordingPlayerLogic)
    const { jumpTimeMs } = useValues(sessionRecordingPlayerLogic)

    const altKeyHeld = useKeyHeld('Alt')
    const jumpTimeSeconds = altKeyHeld ? 1 : jumpTimeMs / 1000
    const altKeyName = navigator.platform.includes('Mac') ? '⌥' : 'Alt'

    const arrowSymbol = direction === 'forward' ? '→' : '←'
    const arrowName = direction === 'forward' ? 'right' : 'left'

    return (
        <Tooltip
            placement="top"
            title={
                <div className="text-center">
                    {!altKeyHeld ? (
                        <>
                            {capitalizeFirstLetter(direction)} {jumpTimeSeconds}s (
                            <kbd>
                                {arrowSymbol} {arrowName} arrow
                            </kbd>
                            ) <br />
                        </>
                    ) : null}
                    {capitalizeFirstLetter(direction)} 1 frame ({ONE_FRAME_MS}ms) (
                    <kbd>
                        {altKeyName} + {arrowSymbol}
                    </kbd>
                    )
                </div>
            }
        >
            <LemonButton
                data-attr={`seek-skip-${direction}`}
                size="small"
                onClick={() => (direction === 'forward' ? seekForward : seekBackward())}
            >
                <div className="PlayerControlSeekIcon">
                    <span className="PlayerControlSeekIcon__seconds">{jumpTimeSeconds}</span>
                    <IconSkipBackward
                        className={clsx('PlayerControlSeekIcon__icon', {
                            'PlayerControlSeekIcon__icon--forward': direction === 'forward',
                        })}
                    />
                </div>
            </LemonButton>
        </Tooltip>
    )
}
