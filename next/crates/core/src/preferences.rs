use serde::{Deserialize, Serialize};
use url::Url;
use uuid::Uuid;

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Theme {
    #[default]
    System,
    Light,
    Dark,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Density {
    Comfortable,
    Compact,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(try_from = "ServiceInput")]
pub struct PersonalService {
    pub id: Uuid,
    pub name: String,
    pub url: Url,
}

#[derive(Deserialize)]
struct ServiceInput {
    id: Uuid,
    name: String,
    url: String,
}
impl TryFrom<ServiceInput> for PersonalService {
    type Error = &'static str;
    fn try_from(input: ServiceInput) -> Result<Self, Self::Error> {
        let mut service = Self::new(&input.name, &input.url)?;
        service.id = input.id;
        Ok(service)
    }
}
impl PersonalService {
    pub fn new(name: &str, address: &str) -> Result<Self, &'static str> {
        let name = name.trim();
        if name.is_empty() || name.chars().count() > 80 || name.chars().any(char::is_control) {
            return Err("Название: от 1 до 80 символов");
        }
        let url = Url::parse(address.trim()).map_err(|_| "Введите полный адрес сайта")?;
        if !matches!(url.scheme(), "https" | "http")
            || url.host_str().is_none()
            || !url.username().is_empty()
            || url.password().is_some()
        {
            return Err("Нужна ссылка http или https без логина и пароля");
        }
        Ok(Self {
            id: Uuid::new_v4(),
            name: name.into(),
            url,
        })
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default)]
pub struct Preferences {
    pub employee_id: Uuid,
    pub theme: Theme,
    pub density: Option<Density>,
    pub accent: [u8; 3],
    pub reduced_motion: bool,
    pub proactive_search: bool,
    pub services: Vec<PersonalService>,
}
impl Default for Preferences {
    fn default() -> Self {
        Self {
            employee_id: Uuid::nil(),
            theme: Theme::System,
            density: None,
            accent: [111, 100, 236],
            reduced_motion: false,
            proactive_search: false,
            services: vec![],
        }
    }
}
impl Preferences {
    pub fn add_service(&mut self, service: PersonalService) -> Result<(), &'static str> {
        if self.services.len() >= 50 {
            return Err("Можно добавить не больше 50 личных сервисов");
        }
        if self.services.iter().any(|s| s.url == service.url) {
            return Err("Эта ссылка уже добавлена");
        }
        if self.services.iter().any(|s| s.id == service.id) {
            return Err("Такой идентификатор сервиса уже используется");
        }
        self.services.push(service);
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn services_reject_executable_links_and_credentials() {
        for url in [
            "javascript:alert(1)",
            "file:///c:/secret",
            "https://user:pass@example.org",
            "data:text/html,hi",
        ] {
            assert!(PersonalService::new("Сервис", url).is_err());
        }
        assert!(PersonalService::new("Карты", "https://example.org/maps").is_ok());
    }
    #[test]
    fn preferences_roundtrip_and_no_duplicates() {
        let mut p = Preferences {
            theme: Theme::Dark,
            density: Some(Density::Compact),
            ..Default::default()
        };
        p.add_service(PersonalService::new("Карты", "https://example.org").unwrap())
            .unwrap();
        assert!(
            p.add_service(PersonalService::new("Другие карты", "https://example.org/").unwrap())
                .is_err()
        );
        let copy: Preferences = serde_json::from_str(&serde_json::to_string(&p).unwrap()).unwrap();
        assert_eq!(copy.theme, Theme::Dark);
        assert_eq!(copy.density, Some(Density::Compact));
        assert_eq!(copy.services.len(), 1);
        assert!(serde_json::from_str::<PersonalService>(r#"{"id":"00000000-0000-0000-0000-000000000000","name":"X","url":"javascript:evil()"}"#).is_err());
    }
}
