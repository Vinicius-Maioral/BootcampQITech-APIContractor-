from decimal import Decimal

from errors.base_error import QIException


class NotFoundSampleEntity(QIException):
    code = "QIT001001"

    def __init__(self, sample_entity_key) -> None:
        title = "Entity not Found"
        http_status = 404
        description = f"Entity with key {sample_entity_key} was not found."
        translation = f"A entidade com chave {sample_entity_key} não foi encontrada."
        super().__init__(title, self.code, http_status, description, translation)


class SampleEntityFinalStatus(QIException):
    code = "QIT001002"

    def __init__(self, old_status, new_status) -> None:
        title = "Entity cannot change status"
        http_status = 409
        description = f"Entity with status {old_status} cannot update to {new_status}."
        translation = "Essa entidade não pode ser atualizada."
        super().__init__(title, self.code, http_status, description, translation)


class InvalidDocumentNumber(QIException):
    """O CPF tem o formato certo e não existe.

    422, e não 400, de propósito: 400 quer dizer "não consegui ler o seu
    pedido". Aqui a API leu, entendeu, e o valor é que não pode existir —
    os dois últimos dígitos não batem com a conta. A diferença está
    explicada em src/utils/document_number.py.
    """

    code = "QIT001003"

    def __init__(self, document_number) -> None:
        title = "Invalid Document Number"
        http_status = 422
        description = f"The document number {document_number} is not a valid CPF."
        translation = "O CPF informado não é válido."
        super().__init__(title, self.code, http_status, description, translation)


class DuplicatedDocumentNumber(QIException):
    """Já existe um cadastro com este CPF.

    409 Conflict: o pedido está correto em si, e o que impede é o que já
    está no banco. É a mesma família do SampleEntityFinalStatus aqui em
    cima — conflito com o que já existe, não erro de quem pediu.
    """

    code = "QIT001004"

    def __init__(self, document_number) -> None:
        title = "Document Number already registered"
        http_status = 409
        description = f"There is already an entity with the document number {document_number}."
        translation = "Já existe um cadastro com este CPF."
        super().__init__(title, self.code, http_status, description, translation)


class DuplicatedEmail(QIException):
    code = "QIT001005"

    def __init__(self, email) -> None:
        title = "Email already registered"
        http_status = 409
        description = f"There is already an entity with the email {email}."
        translation = "Já existe um cadastro com este e-mail."
        super().__init__(title, self.code, http_status, description, translation)


class UnderageSampleEntity(QIException):
    code = "QIT001006"

    def __init__(self, age, minimum_age) -> None:
        title = "Entity is underage"
        http_status = 422
        description = f"The entity is {age} years old, and the minimum is {minimum_age}."
        translation = f"É preciso ter pelo menos {minimum_age} anos."
        super().__init__(title, self.code, http_status, description, translation)


class InvalidBirthdate(QIException):
    """A data tem o formato certo e não existe no calendário.

    Existe porque o `pattern` do schema sabe contar dígitos, não dias:
    "2025-02-30" e "9999-99-99" passam pelo regex e morrem no
    `date.fromisoformat`. Sem esta classe, esse ValueError virava 500 —
    a API culpando a si mesma por um erro de quem chamou.
    """

    code = "QIT001007"

    def __init__(self, birthdate) -> None:
        title = "Invalid Birthdate"
        http_status = 422
        description = f"The birthdate {birthdate} is not a real date."
        translation = "A data de nascimento informada não existe."
        super().__init__(title, self.code, http_status, description, translation)


class ContractNotFound(QIException):
    code = "QIT003001"

    def __init__(self, contract_key: str) -> None:
        title = "Not Found"
        http_status = 404
        description = f"Contract with key {contract_key} was not found."
        translation = f"O contrato com chave {contract_key} não foi encontrado."
        super().__init__(title, self.code, http_status, description, translation)


class DuplicatedExternalReference(QIException):
    code = "QIT003002"

    def __init__(self, client_key: str, external_reference: str) -> None:
        title = "Conflict"
        http_status = 409
        description = f"A contract with external reference {external_reference} already exists for client {client_key}."
        translation = f"Já existe um contrato com a referência externa {external_reference} para o cliente {client_key}."
        super().__init__(title, self.code, http_status, description, translation)


class InvalidContractTransition(QIException):
    code = "QIT003003"

    def __init__(self, old_status: str, new_status: str) -> None:
        title = "Conflict"
        http_status = 409
        description = f"Contract status cannot change from {old_status} to {new_status}."
        translation = f"O status do contrato não pode mudar de {old_status} para {new_status}."
        super().__init__(title, self.code, http_status, description, translation)


class InvalidContractRoles(QIException):
    code = "QIT003004"

    def __init__(self, roles: str) -> None:
        title = "Unprocessable Entity"
        http_status = 422
        description = f"The contract role configuration is invalid: {roles}."
        translation = f"A configuração de papéis do contrato é inválida: {roles}."
        super().__init__(title, self.code, http_status, description, translation)


class LandlordIsTenant(QIException):
    code = "QIT003005"

    def __init__(self, party_key: str) -> None:
        title = "Unprocessable Entity"
        http_status = 422
        description = f"Party {party_key} cannot be both landlord and tenant in the same contract."
        translation = f"A parte {party_key} não pode ser locadora e locatária no mesmo contrato."
        super().__init__(title, self.code, http_status, description, translation)


class InvalidSplitBeneficiary(QIException):
    code = "QIT003006"

    def __init__(self, party_key: str) -> None:
        title = "Unprocessable Entity"
        http_status = 422
        description = f"Party {party_key} is not a valid beneficiary for this split."
        translation = f"A parte {party_key} não é uma beneficiária válida para este rateio."
        super().__init__(title, self.code, http_status, description, translation)


class PartyNotActiveInRegistry(QIException):
    code = "QIT003007"

    def __init__(self, party_key: str) -> None:
        title = "Conflict"
        http_status = 409
        description = f"Party {party_key} is not active in the registry."
        translation = f"A parte {party_key} não está ativa no Cadastro."
        super().__init__(title, self.code, http_status, description, translation)


class PartyNotFoundInRegistry(QIException):
    code = "QIT003008"

    def __init__(self, party_key: str) -> None:
        title = "Unprocessable Entity"
        http_status = 422
        description = f"Party {party_key} was not found in the registry."
        translation = f"A parte {party_key} não foi encontrada no Cadastro."
        super().__init__(title, self.code, http_status, description, translation)


class LedgerRefusedSplit(QIException):
    code = "QIT003009"

    def __init__(self, contract_key: str) -> None:
        title = "Unprocessable Entity"
        http_status = 422
        description = f"The Ledger refused the split for contract {contract_key}."
        translation = f"O Ledger recusou o rateio do contrato {contract_key}."
        super().__init__(title, self.code, http_status, description, translation)


class DependencyUnavailable(QIException):
    code = "QIT003010"

    def __init__(self, dependency_name: str) -> None:
        title = "Service Unavailable"
        http_status = 503
        description = f"Dependency {dependency_name} is temporarily unavailable."
        translation = f"O serviço {dependency_name} está temporariamente indisponível."
        super().__init__(title, self.code, http_status, description, translation)


class AdministratorIsNotClient(QIException):
    code = "QIT003011"

    def __init__(self, party_key: str, client_key: str) -> None:
        title = "Unprocessable Entity"
        http_status = 422
        description = f"Administrator {party_key} does not correspond to client {client_key}."
        translation = f"A parte administradora {party_key} não corresponde ao cliente {client_key}."
        super().__init__(title, self.code, http_status, description, translation)


class SplitPercentagesTooHigh(QIException):
    code = "QIT003012"

    def __init__(self, total_percentage: Decimal) -> None:
        title = "Unprocessable Entity"
        http_status = 422
        description = f"The split percentage total of {total_percentage}% exceeds the allowed limit."
        translation = f"A soma dos percentuais do rateio, {total_percentage}%, excede o limite permitido."
        super().__init__(title, self.code, http_status, description, translation)


class LedgerConflict(QIException):
    code = "QIT003013"

    def __init__(self, contract_key: str, operation: str = "split") -> None:
        title = "Conflict"
        http_status = 409
        description = f"The Ledger reported a conflict for {operation} of contract {contract_key}."
        translation = f"O Ledger informou um conflito na operação {operation} do contrato {contract_key}."
        super().__init__(title, self.code, http_status, description, translation)


class ClientNotFoundInRegistry(QIException):
    code = "QIT003014"

    def __init__(self, client_key: str) -> None:
        super().__init__("Unprocessable Entity", self.code, 422,
                         f"Client {client_key} was not found in the registry.",
                         f"O cliente {client_key} não foi encontrado no Cadastro.")


class ClientNotActiveInRegistry(QIException):
    code = "QIT003015"

    def __init__(self, client_key: str) -> None:
        super().__init__("Conflict", self.code, 409,
                         f"Client {client_key} is not active in the registry.",
                         f"O cliente {client_key} não está ativo no Cadastro.")
