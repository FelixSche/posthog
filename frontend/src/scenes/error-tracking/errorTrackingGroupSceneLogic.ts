import { actions, connect, kea, key, listeners, path, props, reducers, selectors } from 'kea'
import { loaders } from 'kea-loaders'
import { actionToUrl, router, urlToAction } from 'kea-router'
import api from 'lib/api'
import { Dayjs, dayjs } from 'lib/dayjs'
import { Scene } from 'scenes/sceneTypes'
import { urls } from 'scenes/urls'

import { ErrorTrackingIssue } from '~/queries/schema'
import { Breadcrumb } from '~/types'

import type { errorTrackingGroupSceneLogicType } from './errorTrackingGroupSceneLogicType'
import { errorTrackingLogic } from './errorTrackingLogic'
import { errorTrackingIssueEventsQuery, errorTrackingIssueQuery } from './queries'

export interface ErrorTrackingEvent {
    uuid: string
    timestamp: Dayjs
    properties: Record<string, any>
    person: {
        distinct_id: string
        uuid?: string
        created_at?: string
        properties?: Record<string, any>
    }
}

export interface ErrorTrackingGroupSceneLogicProps {
    id: ErrorTrackingIssue['id']
}

export enum IssueTab {
    Overview = 'overview',
    Breakdowns = 'breakdowns',
}

export const errorTrackingGroupSceneLogic = kea<errorTrackingGroupSceneLogicType>([
    path((key) => ['scenes', 'error-tracking', 'errorTrackingGroupSceneLogic', key]),
    props({} as ErrorTrackingGroupSceneLogicProps),
    key((props) => props.id),

    connect({
        values: [errorTrackingLogic, ['dateRange', 'filterTestAccounts', 'filterGroup', 'hasGroupActions']],
    }),

    actions({
        setTab: (tab: IssueTab) => ({ tab }),
        setActiveEventUUID: (uuid: ErrorTrackingEvent['uuid']) => ({ uuid }),
        updateIssue: (issue: Partial<Pick<ErrorTrackingIssue, 'assignee' | 'status'>>) => ({ issue }),
    }),

    reducers(() => ({
        tab: [
            IssueTab.Overview as IssueTab,
            {
                setTab: (_, { tab }) => tab,
            },
        ],
        activeEventUUID: [
            undefined as ErrorTrackingEvent['uuid'] | undefined,
            {
                setActiveEventUUID: (_, { uuid }) => uuid,
            },
        ],
    })),

    loaders(({ props, values }) => ({
        issue: [
            null as ErrorTrackingIssue | null,
            {
                loadIssue: async () => {
                    const response = await api.query(
                        errorTrackingIssueQuery({
                            issueId: props.id,
                            dateRange: values.dateRange,
                            filterTestAccounts: values.filterTestAccounts,
                            filterGroup: values.filterGroup,
                        }),
                        {},
                        undefined,
                        true
                    )

                    // ErrorTrackingQuery returns a list of issues
                    // when a fingerprint is supplied there will only be a single issue
                    return response.results[0]
                },
                updateIssue: async ({ issue }) => {
                    const response = await api.errorTracking.updateIssue(props.id, issue)
                    return { ...values.issue, ...response }
                },
            },
        ],
        events: [
            [] as ErrorTrackingEvent[],
            {
                loadEvents: async () => {
                    const response = await api.query(
                        errorTrackingIssueEventsQuery({
                            select: ['uuid', 'properties', 'timestamp', 'person'],
                            issueId: props.id,
                            dateRange: values.dateRange,
                            filterTestAccounts: values.filterTestAccounts,
                            filterGroup: values.filterGroup,
                            offset: values.events.length,
                        })
                    )

                    const newResults = response.results.map((r) => ({
                        uuid: r[0],
                        properties: JSON.parse(r[1]),
                        timestamp: dayjs(r[2]),
                        person: r[3],
                    }))

                    return [...values.events, ...newResults]
                },
            },
        ],
    })),

    listeners(({ values, actions }) => ({
        loadIssueSuccess: () => {
            actions.loadEvents()
        },
        loadEventsSuccess: () => {
            if (!values.activeEventUUID) {
                actions.setActiveEventUUID(values.events[0]?.uuid)
            }
        },
    })),

    selectors({
        breadcrumbs: [
            (s) => [s.issue],
            (issue): Breadcrumb[] => {
                const exceptionType = issue?.name || 'Unknown Type'
                return [
                    {
                        key: Scene.ErrorTracking,
                        name: 'Error tracking',
                        path: urls.errorTracking(),
                    },
                    {
                        key: [Scene.ErrorTrackingGroup, exceptionType],
                        name: exceptionType,
                    },
                ]
            },
        ],
    }),

    actionToUrl(({ values }) => ({
        setTab: () => {
            const searchParams = router.values.searchParams

            if (values.tab != IssueTab.Overview) {
                searchParams['tab'] = values.tab
            }

            return [router.values.location.pathname, searchParams]
        },
    })),

    urlToAction(({ actions }) => ({
        [urls.errorTrackingIssue('*')]: (_, searchParams) => {
            if (searchParams.tab) {
                actions.setTab(searchParams.tab)
            }
        },
    })),
])
