//! Each navigation starts a new generation. Late results cannot overwrite another section.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Section {
    Overview,
    Search,
    Clients,
    Carriers,
    Tasks,
    Mail,
    Loading,
    Services,
    Team,
    Settings,
}
impl Section {
    pub fn title(self) -> &'static str {
        match self {
            Self::Overview => "Рабочий стол",
            Self::Search => "Поиск клиентов",
            Self::Clients => "Клиентская база",
            Self::Carriers => "Перевозчики",
            Self::Tasks => "Задачи",
            Self::Mail => "Почта",
            Self::Loading => "Погрузка",
            Self::Services => "Сервисы",
            Self::Team => "Сотрудники",
            Self::Settings => "Настройки",
        }
    }
}
#[derive(Debug, Clone, Copy)]
pub struct RequestTicket {
    section: Section,
    generation: u64,
}
pub struct Navigation {
    pub section: Section,
    generation: u64,
}
impl Default for Navigation {
    fn default() -> Self {
        Self {
            section: Section::Overview,
            generation: 0,
        }
    }
}
impl Navigation {
    pub fn navigate(&mut self, section: Section) -> RequestTicket {
        self.section = section;
        self.generation += 1;
        self.ticket()
    }
    pub fn ticket(&self) -> RequestTicket {
        RequestTicket {
            section: self.section,
            generation: self.generation,
        }
    }
    pub fn accepts(&self, ticket: RequestTicket) -> bool {
        ticket.section == self.section && ticket.generation == self.generation
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn client_response_never_overwrites_carriers_even_after_return() {
        let mut n = Navigation::default();
        let late = n.navigate(Section::Clients);
        let carriers = n.navigate(Section::Carriers);
        assert!(!n.accepts(late));
        assert!(n.accepts(carriers));
        n.navigate(Section::Clients);
        assert!(!n.accepts(late));
        assert!(!n.accepts(carriers));
    }
}
