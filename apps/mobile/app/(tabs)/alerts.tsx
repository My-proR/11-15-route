import { FlatList, RefreshControl, View } from "react-native";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../src/api";
import { useT } from "../../src/i18n";
import { Btn, Card, Txt, useTheme } from "../../src/ui";

interface N { id: string; title: string; body: string; critical: boolean; read_at: string | null; created_at: string }
export default function Alerts() {
  const t = useTheme();
  const tr = useT();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["notifs"], queryFn: () => api<{ items: N[] }>("/notifications"), refetchInterval: 30_000 });
  const read = useMutation({ mutationFn: (id: string) => api(`/notifications/${id}/read`, { method: "POST" }), onSuccess: () => qc.invalidateQueries({ queryKey: ["notifs"] }) });
  return (
    <FlatList style={{ backgroundColor: t.bg }} contentContainerStyle={{ padding: 16, gap: 10 }} data={q.data?.items ?? []} keyExtractor={(n) => n.id}
      refreshControl={<RefreshControl refreshing={q.isFetching} onRefresh={() => void q.refetch()} />}
      ListEmptyComponent={<Card><Txt muted>{tr("caughtUp")}</Txt></Card>}
      renderItem={({ item }) => <Card title={item.title + (item.critical ? " (critical)" : "")}><Txt muted>{item.body}</Txt><View />{!item.read_at && <Btn kind="secondary" label={tr("markRead")} onPress={() => read.mutate(item.id)} />}</Card>} />
  );
}
