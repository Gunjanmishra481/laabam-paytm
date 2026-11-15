// Placeholder for screens later phases build (Profit, Plan, Ask, ...). Keeps the tab bar working meanwhile.

import { Button, Screen, type TabKey } from '../ui'
import { useSession } from '../useSession'

export default function NotYet({ tab }: { tab?: TabKey }) {
  const { t } = useSession()
  const title = tab === 'profit' ? t('tab_profit') : tab === 'plan' ? t('tab_plan') : t('appName')
  return (
    <Screen tab={tab}>
      <h1 className="screen-title">{title}</h1>
      <p className="note">{t('notYet')}</p>
      {!tab && (
        <Button variant="secondary" to="/">
          {t('tab_counter')}
        </Button>
      )}
    </Screen>
  )
}
