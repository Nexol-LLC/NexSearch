use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, BTreeSet};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Folder {
    pub id: String,
    pub title: String,
    pub delimiter: Option<char>,
    pub selectable: bool,
}
#[derive(Debug, Clone)]
pub struct FolderNode {
    pub folder: Folder,
    pub depth: usize,
}
pub fn folder_tree(folders: &[Folder]) -> Vec<FolderNode> {
    let mut sorted = folders.to_vec();
    sorted.sort_by_key(|f| (f.id.to_uppercase() != "INBOX", f.id.to_lowercase()));
    sorted
        .into_iter()
        .map(|f| {
            let depth = f.delimiter.map_or(0, |d| f.id.matches(d).count());
            FolderNode { folder: f, depth }
        })
        .collect()
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Message {
    pub id: String,
    pub message_id: String,
    pub in_reply_to: Option<String>,
    pub references: Vec<String>,
    pub subject: String,
    pub sender: String,
    pub body: String,
    pub sent_at: i64,
    pub seen: bool,
}
#[derive(Debug, Clone)]
pub struct Conversation {
    pub messages: Vec<Message>,
}

pub fn conversations(messages: &[Message]) -> Vec<Conversation> {
    // Union by reference headers handles out-of-order replies and missing intermediate messages.
    let mut parent: Vec<usize> = (0..messages.len()).collect();
    fn root(parent: &[usize], mut i: usize) -> usize {
        while parent[i] != i {
            i = parent[i];
        }
        i
    }
    let mut keys = BTreeMap::<String, usize>::new();
    for (i, m) in messages.iter().enumerate() {
        for key in std::iter::once(&m.message_id)
            .chain(m.in_reply_to.iter())
            .chain(m.references.iter())
            .filter(|s| !s.is_empty())
        {
            if let Some(&other) = keys.get(key) {
                let a = root(&parent, i);
                let b = root(&parent, other);
                parent[a] = b;
            } else {
                keys.insert(key.clone(), i);
            }
        }
    }
    let mut groups = BTreeMap::<usize, Vec<Message>>::new();
    for (i, m) in messages.iter().enumerate() {
        groups.entry(root(&parent, i)).or_default().push(m.clone());
    }
    let mut output: Vec<_> = groups
        .into_values()
        .map(|mut ms| {
            ms.sort_by_key(|m| m.sent_at);
            Conversation { messages: ms }
        })
        .collect();
    output.sort_by_key(|c| std::cmp::Reverse(c.messages.last().map_or(0, |m| m.sent_at)));
    output
}
pub fn duplicate_message_ids(messages: &[Message]) -> bool {
    let mut seen = BTreeSet::new();
    messages.iter().any(|m| !seen.insert(&m.id))
}

#[cfg(test)]
mod tests {
    use super::*;
    fn message(id: &str, parent: Option<&str>) -> Message {
        Message {
            id: id.into(),
            message_id: id.into(),
            in_reply_to: parent.map(Into::into),
            references: vec![],
            subject: "Тема".into(),
            sender: "Адрес".into(),
            body: String::new(),
            sent_at: 1,
            seen: false,
        }
    }
    #[test]
    fn threads_join_out_of_order_replies_but_not_unrelated_same_sender() {
        let c = conversations(&[
            message("reply", Some("root")),
            message("other", None),
            message("root", None),
        ]);
        let mut sizes: Vec<_> = c.iter().map(|t| t.messages.len()).collect();
        sizes.sort();
        assert_eq!(sizes, vec![1, 2]);
        let c = conversations(&[message("a", Some("missing")), message("b", Some("missing"))]);
        assert_eq!(c.len(), 1);
    }
    #[test]
    fn inbox_precedes_nested_folders() {
        let folders = vec![
            Folder {
                id: "Клиенты/Москва".into(),
                title: "Москва".into(),
                delimiter: Some('/'),
                selectable: true,
            },
            Folder {
                id: "INBOX".into(),
                title: "Входящие".into(),
                delimiter: None,
                selectable: true,
            },
        ];
        let nodes = folder_tree(&folders);
        assert_eq!(nodes[0].folder.id, "INBOX");
        assert_eq!(nodes[1].depth, 1);
    }
}
