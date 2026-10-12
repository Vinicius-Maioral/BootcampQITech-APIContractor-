from decimal import Decimal

from errors.base_error import QIException


def _with_source_code(description, source_code):
    return description + (f" Source error code: {source_code}." if source_code else "")


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

    def __init__(self, party_key: str, source_code: str = None) -> None:
        title = "Conflict"
        http_status = 409
        description = _with_source_code(f"Party {party_key} is not active in the registry.", source_code)
        translation = f"A parte {party_key} não está ativa no Cadastro."
        super().__init__(title, self.code, http_status, description, translation)


class PartyNotFoundInRegistry(QIException):
    code = "QIT003008"

    def __init__(self, party_key: str, source_code: str = None) -> None:
        title = "Unprocessable Entity"
        http_status = 422
        description = _with_source_code(f"Party {party_key} was not found in the registry.", source_code)
        translation = f"A parte {party_key} não foi encontrada no Cadastro."
        super().__init__(title, self.code, http_status, description, translation)


class LedgerRefusedSplit(QIException):
    code = "QIT003009"

    def __init__(self, contract_key: str, source_code: str = None) -> None:
        title = "Unprocessable Entity"
        http_status = 422
        description = _with_source_code(f"The Ledger refused the split for contract {contract_key}.", source_code)
        translation = f"O Ledger recusou o rateio do contrato {contract_key}."
        super().__init__(title, self.code, http_status, description, translation)


class DependencyUnavailable(QIException):
    code = "QIT003010"

    def __init__(self, dependency_name: str, source_code: str = None) -> None:
        title = "Service Unavailable"
        http_status = 503
        description = _with_source_code(f"Dependency {dependency_name} is temporarily unavailable.", source_code)
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

    def __init__(self, contract_key: str, operation: str = "split", source_code: str = None) -> None:
        title = "Conflict"
        http_status = 409
        description = _with_source_code(f"The Ledger reported a conflict for {operation} of contract {contract_key}.", source_code)
        translation = f"O Ledger informou um conflito na operação {operation} do contrato {contract_key}."
        super().__init__(title, self.code, http_status, description, translation)

    @classmethod
    def pending_split_differs(cls, contract_key: str) -> "LedgerConflict":
        # Same code, detected by the Contract itself: the Ledger was not called.
        conflict = cls(contract_key)
        conflict.description = f"The pending split version of contract {contract_key} was re-sent with different content."
        conflict.translation = f"A versão pendente do rateio do contrato {contract_key} foi reenviada com conteúdo diferente."
        return conflict
