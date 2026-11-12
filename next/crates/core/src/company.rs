use serde::{Deserialize, Serialize};
use uuid::Uuid;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Role {
    Logistician,
    Manager,
    Director,
}

impl Role {
    pub fn title(self) -> &'static str {
        match self {
            Self::Logistician => "Логист",
            Self::Manager => "Руководитель отдела",
            Self::Director => "Генеральный директор",
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Employee {
    pub id: Uuid,
    pub name: String,
    pub role: Role,
    pub department_id: Uuid,
}

impl Employee {
    pub fn can_view_clients_of(&self, other: &Employee) -> bool {
        self.id == other.id
            || self.role == Role::Director
            || (self.role == Role::Manager
                && self.department_id == other.department_id
                && other.role == Role::Logistician)
    }
    pub fn can_assign_to(&self, other: &Employee) -> bool {
        self.can_view_clients_of(other)
    }
    pub fn can_search(&self) -> bool {
        true
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(try_from = "String", into = "String")]
pub struct Inn(String);

impl Inn {
    pub fn as_str(&self) -> &str {
        &self.0
    }
}
impl From<Inn> for String {
    fn from(value: Inn) -> Self {
        value.0
    }
}

impl TryFrom<String> for Inn {
    type Error = &'static str;
    fn try_from(value: String) -> Result<Self, Self::Error> {
        let value = value.trim();
        if !matches!(value.len(), 10 | 12)
            || !value.bytes().all(|b| b.is_ascii_digit())
            || value.bytes().all(|b| b == b'0')
        {
            return Err("ИНН должен содержать 10 или 12 цифр");
        }
        let digits: Vec<u32> = value.bytes().map(|b| (b - b'0') as u32).collect();
        let check =
            |weights: &[u32]| digits.iter().zip(weights).map(|(d, w)| d * w).sum::<u32>() % 11 % 10;
        let valid = if digits.len() == 10 {
            check(&[2, 4, 10, 3, 5, 9, 4, 6, 8]) == digits[9]
        } else {
            check(&[7, 2, 4, 10, 3, 5, 9, 4, 6, 8]) == digits[10]
                && check(&[3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8]) == digits[11]
        };
        valid
            .then(|| Self(value.to_owned()))
            .ok_or("Не совпадает контрольное число ИНН")
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Evidence {
    pub source: String,
    pub checked_at: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Company {
    pub id: Uuid,
    pub name: String,
    pub inn: Inn,
    /// Unknown status is never silently treated as active.
    pub active: Option<bool>,
    pub evidence: Option<Evidence>,
    pub industry: String,
    pub region: String,
    pub timezone: String,
    pub revenue_rub: Option<u64>,
    pub revenue_year: Option<u16>,
    pub phone: String,
    pub email: String,
    pub routes: Vec<String>,
    pub owner_id: Option<Uuid>,
    pub stage: String,
}

impl Company {
    pub fn verified_active(&self) -> bool {
        self.active == Some(true) && self.evidence.is_some()
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SearchCriteria {
    pub count: u8,
    pub inn: Option<Inn>,
    pub industries: Vec<String>,
    pub timezones: Vec<String>,
    pub revenue_min: Option<u64>,
    pub revenue_max: Option<u64>,
}

impl Default for SearchCriteria {
    fn default() -> Self {
        Self {
            count: 10,
            inn: None,
            industries: vec![],
            timezones: vec![],
            revenue_min: None,
            revenue_max: None,
        }
    }
}

impl SearchCriteria {
    pub fn validate(&self) -> Result<(), &'static str> {
        if !(1..=50).contains(&self.count) {
            return Err("Количество компаний: от 1 до 50");
        }
        if self
            .revenue_min
            .zip(self.revenue_max)
            .is_some_and(|(min, max)| min >= max)
        {
            return Err("Верхняя граница выручки должна быть больше нижней");
        }
        Ok(())
    }
    pub fn matches(&self, c: &Company) -> bool {
        self.validate().is_ok()
            && c.verified_active()
            && c.owner_id.is_none()
            && self.inn.as_ref().is_none_or(|inn| inn == &c.inn)
            && (self.industries.is_empty() || self.industries.contains(&c.industry))
            && (self.timezones.is_empty() || self.timezones.contains(&c.timezone))
            && self
                .revenue_min
                .is_none_or(|min| c.revenue_rub.is_some_and(|r| r >= min))
            && self
                .revenue_max
                .is_none_or(|max| c.revenue_rub.is_some_and(|r| r < max))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn inn_rejects_missing_invalid_and_zero() {
        for value in ["", "0", "0000000000", "7707083894", "770708389З"] {
            assert!(Inn::try_from(value.to_owned()).is_err(), "{value}");
        }
        for value in ["7707083893", "500100732259"] {
            assert!(Inn::try_from(value.to_owned()).is_ok(), "{value}");
        }
    }
    #[test]
    fn roles_keep_departments_and_private_clients_separate() {
        let first = Uuid::new_v4();
        let second = Uuid::new_v4();
        let mut boss = Employee {
            id: Uuid::new_v4(),
            name: "Руководитель".into(),
            role: Role::Manager,
            department_id: first,
        };
        let mut worker = Employee {
            id: Uuid::new_v4(),
            name: "Логист".into(),
            role: Role::Logistician,
            department_id: first,
        };
        assert!(boss.can_view_clients_of(&worker));
        assert!(!worker.can_view_clients_of(&boss));
        worker.department_id = second;
        assert!(!boss.can_assign_to(&worker));
        boss.role = Role::Director;
        assert!(boss.can_assign_to(&worker));
        for role in [Role::Director, Role::Manager, Role::Logistician] {
            boss.role = role;
            assert!(boss.can_search());
        }
    }
    #[test]
    fn criteria_enforce_limits_and_disjoint_revenue_boundaries() {
        let mut c = SearchCriteria::default();
        assert!(c.validate().is_ok());
        c.count = 0;
        assert!(c.validate().is_err());
        c.count = 51;
        assert!(c.validate().is_err());
        c.count = 50;
        c.revenue_min = Some(100);
        c.revenue_max = Some(100);
        assert!(c.validate().is_err());
    }
}
